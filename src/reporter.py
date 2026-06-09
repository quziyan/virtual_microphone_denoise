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
from typing import Optional

BASE = "https://open.feishu.cn/open-apis"
HTTP_TIMEOUT = 6.0

# token caches (module-level, guarded by _lock)
_lock = threading.Lock()
_tenant = {"value": None, "exp": 0.0}      # tenant_access_token + expiry epoch
_app_token = {"value": None}               # resolved bitable app_token


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


def _send(event: str) -> None:
    cfg = _config()
    if cfg is None:
        return
    app_id, secret, app_token_cfg, wiki, table = cfg
    try:
        token = _tenant_token(app_id, secret)
        app_token = _bitable_app_token(token, app_token_cfg, wiki)
        body = {"fields": {
            "上报时间": int(time.time() * 1000),  # Feishu datetime = epoch millis
            "机器名称": machine_name(),
            "上报类型": event,
        }}
        _request(f"{BASE}/bitable/v1/apps/{app_token}/tables/{table}/records",
                 body=body, token=token)
        _debug(f"reported: {event}")
    except Exception as exc:  # offline / auth / permission — never propagate
        _debug(f"failed ({event}): {exc}")


def report(event: str) -> Optional[threading.Thread]:
    """Append a telemetry row for `event`, off-thread. Returns the thread (so a
    caller like quit can optionally join it briefly), or None if disabled."""
    if not _enabled():
        return None
    t = threading.Thread(target=_send, args=(event,), name="vmic-telemetry", daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    # Live smoke test: send one row and report the outcome.
    os.environ.setdefault("VMIC_TELEMETRY_DEBUG", "1")
    ev = sys.argv[1] if len(sys.argv) > 1 else "测试 / selftest"
    print("enabled:", _enabled(), "| machine:", machine_name())
    _send(ev)
    print("done (check the Bitable for a new row)")
