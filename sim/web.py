"""``BaseHTTPRequestHandler`` factory shared by the local server and the
Vercel function. Static files are served only when ``static_dir`` is given
(locally); on Vercel the ``public/`` directory is served by the platform.
"""
from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

from .app import App

MAX_BODY = 64 * 1024


def make_handler(app: App, static_dir: Path | None = None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TradingBotSim/0.2"

        def log_message(self, fmt, *args):
            pass

        def _send(self, code: int, payload, ctype="application/json"):
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict | None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return None
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                return None
            return body if isinstance(body, dict) else None

        def do_GET(self):
            path = urlparse(self.path).path
            if path.startswith("/api/"):
                code, payload = app.handle("GET", path)
                return self._send(code, payload)
            if static_dir is None:
                return self._send(404, {"error": "not found"})
            if path == "/":
                path = "/index.html"
            file = (static_dir / path.lstrip("/")).resolve()
            if static_dir.resolve() in file.parents and file.is_file():
                ctype = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
                return self._send(200, file.read_bytes(), ctype)
            self._send(404, {"error": "not found"})

        def do_POST(self):
            path = urlparse(self.path).path
            body = self._body()
            if body is None:
                return self._send(400, {"error": "invalid JSON body"})
            code, payload = app.handle("POST", path, body)
            self._send(code, payload)

    return Handler
