from http.server import BaseHTTPRequestHandler
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from darcobfuscator import __version__
from darcobfuscator.obfuscator import obfuscate

_ALLOWED = {
    "roblox_check", "anti_tamper", "rename", "on_fail", "diagnostic",
    "target", "anti_log", "seed",
}
_MAX_BYTES = int(os.environ.get("MAX_BYTES", "1000000"))


def _clean(options):
    if not isinstance(options, dict):
        return {}
    return {k: v for k, v in options.items() if k in _ALLOWED}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._json(200, {"name": "fine", "version": __version__, "status": "ok"})

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("content-length") or 0)
        except ValueError:
            return self._json(400, {"error": "invalid content-length"})
        if length > _MAX_BYTES:
            return self._json(413, {"error": "payload too large"})
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._json(400, {"error": "invalid JSON body"})
        source = payload.get("source")
        if not isinstance(source, str) or not source.strip():
            return self._json(400, {"error": "missing 'source'"})
        try:
            output = obfuscate(source, _clean(payload.get("options")))
        except SyntaxError as e:
            return self._json(400, {"error": "syntax error: " + str(e)})
        except Exception as e:
            return self._json(500, {"error": str(e)})
        self._json(200, {"output": output, "bytes": len(output)})

    def _cors(self):
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
        self.send_header("access-control-allow-headers", "content-type")

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass
