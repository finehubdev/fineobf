from http.server import BaseHTTPRequestHandler
import os
import sys
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fine_store


def _hash_from(path):
    q = parse_qs(urlparse(path).query)
    raw = (q.get("file") or q.get("h") or [""])[0]
    if not raw:
        raw = urlparse(path).path.rsplit("/", 1)[-1]
    if raw.endswith(".lua"):
        raw = raw[:-4]
    return "".join(c for c in raw if c in "0123456789abcdef")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        loader_hash = _hash_from(self.path)
        if not loader_hash:
            return self._text(404, "-- not found")
        record = fine_store.load_loader(loader_hash)
        if not record:
            return self._text(404, "-- not found")
        if record.get("frozen"):
            return self._text(423, "-- this script is frozen by its owner")
        self._text(200, record.get("code") or "")

    def _text(self, code, body):
        data = body.encode()
        self.send_response(code)
        self.send_header("content-type", "text/plain; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.send_header("access-control-allow-origin", "*")
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass
