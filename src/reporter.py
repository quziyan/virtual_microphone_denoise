"""Usage telemetry → Feishu Bitable (多维表格).

Every reportable user action (open app, start, stop, change config…) appends one
row to a Feishu Bitable with three fields: 上报时间, 机器名称, 上报类型.

Design rules:
  - Fire-and-forget: report() returns immediately; the network call runs on a
    daemon thread.
  - Fail-silent: any error (offline, auth, permission) is swallowed — telemetry
    must NEVER block the UI or crash the app.
  - No secrets in this file: credentials/target come from reporter_config.py
    (gitignored, bundled into the .app) or env vars. If neither is present,
    telemetry is simply disabled.

Pure stdlib (urllib) so it bundles cleanly into the frozen .app — no deps.

Feishu flow (the URL is a wiki-wrapped Bitable, so there's an extra hop):
  1. POST auth/v3/tenant_access_token/internal      -> tenant_access_token (~2h)
  2. GET  wiki/v2/spaces/get_node?token=<wiki_node>  -> obj_token (= bitable app_token)
  3. POST bitable/v1/apps/<app_token>/tables/<table>/records  -> append a row

Disable at runtime with env VMIC_TELEMETRY=0 (or false/off).
Set VMIC_TELEMETRY_DEBUG=1 to print failures to stderr while developing.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

BASE = "https://open.feishu.cn/open-apis"
HTTP_TIMEOUT = 6.0

# Offline queue: events persist here immediately, then flush (oldest-first) once
# the network is back. Survives app quit, so nothing is lost while offline.
QUEUE_PATH = (Path.home() / "Library" / "Application Support"
              / "VibeCodingVirMic" / "telemetry_queue.jsonl")
MAX_QUEUE = 2000  # cap so a permanently-offline machine can't grow it unbounded

# token caches (module-level, guarded by _lock)
_lock = threading.Lock()
_tenant = {"value": None, "exp": 0.0}      # tenant_access_token + expiry epoch
_app_token = {"value": None}               # resolved bitable app_token
_ip = {"value": None}                      # cached PUBLIC IP (per process)
_qlock = threading.Lock()                  # guards the queue file
_flush_lock = threading.Lock()             # ensures only one flush runs at a time


def _debug(msg: str) -> None:
    if os.environ.get("VMIC_TELEMETRY_DEBUG"):
        print(f"[telemetry] {msg}", file=sys.stderr)


def _config() -> Optional[tuple[str, str, Optional[str], Optional[str], str]]:
    """(app_id, app_secret, app_token, wiki_node_token, table_id) or None.

    Either app_token (preferred — direct, no wiki scope needed) or wiki_node_token
    (resolved at runtime) must be present. Env vars override the bundled
    reporter_config so a build can be re-targeted without rebuilding.
    """
    app_id = os.environ.get("VMIC_FEISHU_APP_ID")
    secret = os.environ.get("VMIC_FEISHU_APP_SECRET")
    app_token = os.environ.get("VMIC_FEISHU_APP_TOKEN")
    wiki = os.environ.get("VMIC_FEISHU_WIKI_TOKEN")
    table = os.environ.get("VMIC_FEISHU_TABLE_ID")
    if not (app_id and secret and table and (app_token or wiki)):
        try:
            import reporter_config as rc  # gitignored; present in builds
            app_id = app_id or rc.APP_ID
            secret = secret or rc.APP_SECRET
            app_token = app_token or getattr(rc, "APP_TOKEN", None)
            wiki = wiki or getattr(rc, "WIKI_NODE_TOKEN", None)
            table = table or rc.TABLE_ID
        except Exception:
            return None
    if not (app_id and secret and table and (app_token or wiki)):
        return None
    if "x" * 8 in (secret or ""):  # placeholder from the example template
        return None
    return app_id, secret, app_token, wiki, table


def _enabled() -> bool:
    if os.environ.get("VMIC_TELEMETRY", "1").lower() in ("0", "false", "off", "no"):
        return False
    return _config() is not None


def machine_name() -> str:
    """Best-effort human-ish machine name for the 机器名称 column."""
    try:
        return socket.gethostname() or "unknown"
    except Exception:
        return "unknown"


def ip_address() -> str:
    """Best-effort IP for the IP地址 column. Prefers the public IP (meaningful
    across users); falls back to the LAN IP if offline. Cached per process."""
    with _lock:
        if _ip["value"]:
            return _ip["value"]
    try:  # public IP — most useful for telemetry; short timeout
        req = urllib.request.Request(
            "https://api.ipify.org", headers={"User-Agent": "VibeCodingVirMic"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            ip = resp.read().decode("utf-8").strip()
        if ip:
            with _lock:  # only cache a real public IP — so an offline LAN
                _ip["value"] = ip  # fallback later upgrades to the public one
            return ip
    except Exception:
        pass
    try:  # offline fallback: LAN IP (no traffic actually sent on UDP connect)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan = s.getsockname()[0]
        s.close()
        return lan  # NOT cached — retry public IP next time
    except Exception:
        return ""


# -- HTTP helpers ----------------------------------------------------------

def _request(url: str, *, body: dict | None = None, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("code", 0) != 0:
        raise RuntimeError(f"feishu code={payload.get('code')} msg={payload.get('msg')}")
    return payload


def _tenant_token(app_id: str, secret: str) -> str:
    now = time.time()
    with _lock:
        if _tenant["value"] and now < _tenant["exp"] - 60:
            return _tenant["value"]
    payload = _request(f"{BASE}/auth/v3/tenant_access_token/internal",
                       body={"app_id": app_id, "app_secret": secret})
    tok = payload["tenant_access_token"]
    with _lock:
        _tenant["value"] = tok
        _tenant["exp"] = now + float(payload.get("expire", 7200))
    return tok


def _bitable_app_token(token: str, app_token: Optional[str], wiki_node: Optional[str]) -> str:
    if app_token:  # configured directly — no wiki:wiki:readonly scope needed
        return app_token
    with _lock:
        if _app_token["value"]:
            return _app_token["value"]
    payload = _request(
        f"{BASE}/wiki/v2/spaces/get_node?token={wiki_node}&obj_type=wiki",
        token=token)
    obj = payload["data"]["node"]["obj_token"]
    with _lock:
        _app_token["value"] = obj
    return obj


# -- offline queue ---------------------------------------------------------

def _read_queue() -> list[dict]:
    try:
        with _qlock:
            if not QUEUE_PATH.exists():
                return []
            lines = QUEUE_PATH.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out = []
    for ln in lines:
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out


def _write_queue(records: list[dict]) -> None:
    try:
        with _qlock:
            QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
            body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
            QUEUE_PATH.write_text(body, encoding="utf-8")
    except Exception:
        pass


def _enqueue(record: dict) -> None:
    """Append one event to the on-disk queue (so it survives offline + quit)."""
    try:
        with _qlock:
            QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(QUEUE_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        return
    # Enforce the cap (drop oldest) — cheap given the low event rate.
    q = _read_queue()
    if len(q) > MAX_QUEUE:
        _write_queue(q[-MAX_QUEUE:])


def _send_record(record: dict) -> None:
    """Send one queued record. Raises on any failure (so the queue keeps it)."""
    cfg = _config()
    if cfg is None:
        raise RuntimeError("telemetry unconfigured")
    app_id, secret, app_token_cfg, wiki, table = cfg
    token = _tenant_token(app_id, secret)
    app_token = _bitable_app_token(token, app_token_cfg, wiki)
    body = {"fields": {
        # Use the ORIGINAL event time, not flush time, so the row is accurate.
        "上报时间": int(record.get("ts_ms") or time.time() * 1000),
        "机器名称": record.get("machine") or machine_name(),
        "IP地址": ip_address(),
        "上报类型": record.get("event", ""),
    }}
    _request(f"{BASE}/bitable/v1/apps/{app_token}/tables/{table}/records",
             body=body, token=token)


def _flush() -> None:
    """Send all queued events oldest-first; stop at the first failure (offline)."""
    if not _enabled():
        return
    if not _flush_lock.acquire(blocking=False):
        return  # another flush is already draining the queue
    try:
        queue = _read_queue()
        if not queue:
            return
        sent = 0
        for record in queue:
            try:
                _send_record(record)
                sent += 1
            except Exception as exc:  # still offline / error — keep the rest
                _debug(f"flush stopped after {sent}: {exc}")
                break
        if sent:
            _write_queue(queue[sent:])  # drop the ones that went through
            _debug(f"flushed {sent}, {len(queue) - sent} remaining")
    finally:
        _flush_lock.release()


def report(event: str) -> Optional[threading.Thread]:
    """Record a telemetry event. Persists it to the on-disk queue immediately
    (so it's never lost — offline or app-quit), then tries to flush in the
    background. Returns the worker thread (quit can join it briefly) or None if
    disabled. Never blocks the UI; never raises."""
    if not _enabled():
        return None
    record = {
        "ts_ms": int(time.time() * 1000),  # capture event time now, send later
        "machine": machine_name(),
        "event": event,
    }

    def _run() -> None:
        _enqueue(record)  # durable first — survives offline and quit
        _flush()          # then try to drain this + any backlog, oldest-first

    t = threading.Thread(target=_run, name="vmic-telemetry", daemon=True)
    t.start()
    return t


def flush_async() -> Optional[threading.Thread]:
    """Try to drain the offline queue in the background (call on launch + on a
    timer so a backlog sends once the network returns, even with no new event)."""
    if not _enabled():
        return None
    t = threading.Thread(target=_flush, name="vmic-telemetry-flush", daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    # Live smoke test: queue one event and flush.
    os.environ.setdefault("VMIC_TELEMETRY_DEBUG", "1")
    ev = sys.argv[1] if len(sys.argv) > 1 else "测试 / selftest"
    print("enabled:", _enabled(), "| machine:", machine_name(), "| queue:", QUEUE_PATH)
    t = report(ev)
    if t:
        t.join(timeout=15)
    print("pending in queue:", len(_read_queue()))
    print("done (check the Bitable for a new row)")
