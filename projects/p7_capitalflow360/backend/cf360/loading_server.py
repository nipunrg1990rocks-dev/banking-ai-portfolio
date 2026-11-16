"""A tiny placeholder that answers on $PORT while the container migrates and loads the baseline.

    python -m cf360.loading_server      (started in the background by docker-entrypoint.sh)

Hosts such as Back4app and Render check that something answers HTTP on the port soon after start. The first
baseline load can take several minutes on a small instance with a remote database, so this answers in the
meantime: /api/health returns {"status": "loading"} and every other path a short "loading" page. It uses only
the standard library and a few MB of memory, and the entrypoint stops it before starting the real server.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = b"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta http-equiv="refresh" content="20">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>CapitalFlow 360</title></head>
<body style="font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem;line-height:1.5">
<h1>CapitalFlow 360 is starting</h1><p>The demo is loading its synthetic data. This page refreshes by itself;
it usually takes a few minutes the first time.</p></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        if self.path.startswith("/api/"):
            status = 200 if self.path.startswith("/api/health") else 503
            self._send(json.dumps({"status": "loading"}).encode(), "application/json", status)
        else:
            self._send(PAGE, "text/html; charset=utf-8")

    do_HEAD = do_GET

    def log_message(self, *args) -> None:  # keep the deploy log readable
        pass


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8000"))), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
