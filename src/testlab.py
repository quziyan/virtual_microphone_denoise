#!/usr/bin/env python3
"""Hush Test Lab — record A/B tests and compare them in a web page.

Each test records a few seconds from your microphone (the *raw* signal) and the
same audio run through Hush (the *processed* signal), saved as a pair of WAVs.
A local web page lists every test and lets you select one and listen to / look
at raw vs processed side by side.

Run:
    .venv/bin/python src/testlab.py            # serves http://localhost:8675
    open http://localhost:8675

No external dependencies beyond numpy + sounddevice (already installed). The
recorder reads the mic directly, so it works whether or not the virtual mic is
running. If your mic is busy, stop the menu-bar app first.
"""

from __future__ import annotations

import json
import re
import sys
import time
import wave
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from denoise_file import denoise  # noqa: E402  (reuses the verified Hush pipeline)
from engine import resolve_device  # noqa: E402

WEB_DIR = REPO_ROOT / "web"
RECORDINGS = REPO_ROOT / "recordings"
SAMPLERATE = 48000
DEFAULT_MIC = "MacBook Pro"  # raw hardware mic; override with ?mic= or env
PORT = 8675

_SAFE = re.compile(r"[^a-zA-Z0-9_-]+")


def _save_wav(path: Path, audio: np.ndarray, sr: int) -> None:
    pcm = (audio * 32768.0).clip(-32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def _rms(a: np.ndarray) -> float:
    return float(np.sqrt(np.mean(a ** 2)) + 1e-12)


def record_test(seconds: float, atten: float, label: str, mic: str) -> dict:
    """Record raw + Hush-processed audio and persist a test folder."""
    import sounddevice as sd

    dev = resolve_device(sd, mic, "input", None)
    dev_name = sd.query_devices(dev)["name"]
    sr = SAMPLERATE
    n = int(seconds * sr)

    raw = sd.rec(n, samplerate=sr, channels=1, dtype="float32", device=dev)
    sd.wait()
    raw = raw[:, 0]

    processed = denoise(raw, sr, atten)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = _SAFE.sub("-", label).strip("-") or "test"
    tid = f"{stamp}-{safe}"
    folder = RECORDINGS / tid
    folder.mkdir(parents=True, exist_ok=True)
    _save_wav(folder / "raw.wav", raw, sr)
    _save_wav(folder / "processed.wav", processed, sr)

    raw_rms, proc_rms = _rms(raw), _rms(processed)
    meta = {
        "id": tid,
        "label": label or "test",
        "created": datetime.now().isoformat(timespec="seconds"),
        "seconds": round(len(raw) / sr, 2),
        "atten": atten,
        "samplerate": sr,
        "device": dev_name,
        "raw_rms": round(raw_rms, 5),
        "processed_rms": round(proc_rms, 5),
        "reduction_db": round(20 * float(np.log10(proc_rms / raw_rms)), 1),
        "change_rms": round(_rms(raw - processed), 5),
    }
    (folder / "meta.json").write_text(json.dumps(meta, indent=2))
    return meta


def list_tests() -> list[dict]:
    out = []
    if RECORDINGS.exists():
        for folder in sorted(RECORDINGS.iterdir(), reverse=True):
            mj = folder / "meta.json"
            if mj.is_file():
                try:
                    out.append(json.loads(mj.read_text()))
                except json.JSONDecodeError:
                    continue
    return out


def delete_test(tid: str) -> bool:
    safe = _SAFE.sub("-", tid)  # never allow traversal
    folder = RECORDINGS / safe
    if folder.is_dir() and folder.parent == RECORDINGS:
        for f in folder.iterdir():
            f.unlink()
        folder.rmdir()
        return True
    return False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):  # quieter console
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, ctype: str):
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "none")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        if path == "/favicon.ico":
            self.send_response(204)  # no favicon; avoid noisy 404s
            self.end_headers()
            return
        if path == "/" or path == "/index.html":
            return self._send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
        if path == "/app.js":
            return self._send_file(WEB_DIR / "app.js", "text/javascript; charset=utf-8")
        if path == "/style.css":
            return self._send_file(WEB_DIR / "style.css", "text/css; charset=utf-8")
        if path == "/api/tests":
            return self._send_json(list_tests())
        if path.startswith("/recordings/"):
            rel = path[len("/recordings/"):]
            parts = rel.split("/")
            if len(parts) == 2 and parts[1] in ("raw.wav", "processed.wav"):
                safe = _SAFE.sub("-", parts[0])
                f = RECORDINGS / safe / parts[1]
                if f.is_file():
                    return self._send_file(f, "audio/wav")
            return self._send_json({"error": "not found"}, 404)
        return self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/record":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._send_json({"error": "bad json"}, 400)
            seconds = float(body.get("seconds", 6))
            atten = float(body.get("atten", 100))
            label = str(body.get("label", "")).strip()
            mic = str(body.get("mic", DEFAULT_MIC))
            try:
                meta = record_test(seconds, atten, label, mic)
                return self._send_json(meta)
            except Exception as exc:  # surface to the UI rather than 500-with-no-detail
                return self._send_json(
                    {"error": f"{type(exc).__name__}: {exc}"}, 500
                )
        if u.path == "/api/delete":
            qs = parse_qs(u.query)
            tid = (qs.get("id") or [""])[0]
            return self._send_json({"deleted": delete_test(tid)})
        return self._send_json({"error": "not found"}, 404)


def main() -> None:
    RECORDINGS.mkdir(exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Hush Test Lab → http://localhost:{PORT}")
    print(f"  recordings dir: {RECORDINGS}")
    print("  Ctrl+C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        srv.shutdown()


if __name__ == "__main__":
    main()
