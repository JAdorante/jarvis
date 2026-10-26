"""HTTP hologram server. The page reads state from the shared board."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from jarvis.visual.board import publish, snapshot, subscribe, unsubscribe

PAGE_PATH = Path(__file__).resolve().parents[2] / "jarvis_face.html"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/state":
            self._json(snapshot())
            return
        if path == "/events":
            self._events()
            return
        if path in ("/", "/index.html"):
            body = PAGE_PATH.read_text(encoding="utf-8").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/state":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            incoming = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_error(400)
            return
        chosen = incoming.get("state", "")
        try:
            payload = publish(chosen, incoming.get("activity", ""))
        except ValueError:
            self.send_error(400)
            return
        self._json(payload)

    def _json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        subscriber = subscribe()
        try:
            self.wfile.write(b"data: " + json.dumps(snapshot()).encode() + b"\n\n")
            self.wfile.flush()
            while True:
                payload = subscriber.get()
                self.wfile.write(b"data: " + json.dumps(payload).encode() + b"\n\n")
                self.wfile.flush()
        except (OSError, ConnectionError, BrokenPipeError):
            return
        finally:
            unsubscribe(subscriber)

    def log_message(self, fmt, *args):
        return


def serve(host="0.0.0.0", port=8765, background=False):
    server = ThreadingHTTPServer((host, port), Handler)
    if background:
        import threading

        thread = threading.Thread(target=server.serve_forever, name="jarvis-face", daemon=True)
        thread.start()
        return server
    print(f"Jarvis face at http://127.0.0.1:{port}")
    server.serve_forever()
    return server
