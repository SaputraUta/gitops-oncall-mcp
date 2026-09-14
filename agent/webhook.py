"""Grafana alert webhook."""

from __future__ import annotations

import json
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

alerts: queue.Queue[str] = queue.Queue()


def describe(payload: dict) -> str | None:
    """A prompt describing the firing alerts, or None when none are firing."""
    firing = [a for a in payload.get("alerts", []) if a.get("status") == "firing"]
    if not firing:
        return None
    lines = [
        f"- {a.get('labels', {}).get('alertname', '?')} "
        f"(severity={a.get('labels', {}).get('severity', '?')}, "
        f"namespace={a.get('labels', {}).get('namespace', '?')}): "
        f"{a.get('annotations', {}).get('summary', '')}".rstrip()
        for a in firing
    ]
    return "An alert just fired. Triage it and report what you find.\n" + "\n".join(lines)


class _Handler(BaseHTTPRequestHandler):
    token = ""

    def do_POST(self) -> None:
        if self.headers.get("Authorization") != f"Bearer {self.token}":
            self.send_response(401)
            self.end_headers()
            return
        try:
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            prompt = describe(json.loads(body))
        except Exception:
            self.send_response(400)
            self.end_headers()
            return
        # 202 rather than 200: triage takes tens of seconds, and holding the
        # connection open would make Grafana time out and resend the alert.
        self.send_response(202)
        self.end_headers()
        if prompt:
            alerts.put(prompt)

    def log_message(self, *args) -> None:
        pass


def serve(port: int = 8080) -> None:
    _Handler.token = os.environ["MCP_BEARER_TOKEN"]
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[webhook listening on :{port}]", flush=True)
