from http.server import BaseHTTPRequestHandler
import hashlib
import json
import os
import secrets
import sys
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from darcobfuscator import __version__
from darcobfuscator.obfuscator import obfuscate
import fine_store

_ALLOWED = {"name", "silent", "fast", "rename", "on_fail", "seed"}
_MAX_BYTES = int(os.environ.get("MAX_BYTES", "1000000"))


def _clean(options):
    if not isinstance(options, dict):
        return {}
    opts = {k: v for k, v in options.items() if k in _ALLOWED}
    opts["target"] = "executor"
    return opts


def _build(source, options):
    return obfuscate(source, _clean(options))


def _base(handler):
    env = os.environ.get("PUBLIC_BASE_URL")
    if env:
        return env.rstrip("/")
    host = handler.headers.get("host") or "localhost"
    proto = handler.headers.get("x-forwarded-proto") or (
        "http" if host.startswith("localhost") or host.startswith("127.") else "https")
    return proto + "://" + host


def _loadstring(url):
    return 'loadstring(game:HttpGet("' + url + '"))()'


def _loader_hash(path, query):
    raw = ""
    q = parse_qs(query)
    if q.get("file"):
        raw = q["file"][0]
    elif q.get("h"):
        raw = q["h"][0]
    else:
        raw = path.rsplit("/", 1)[-1]
    if raw.endswith(".lua"):
        raw = raw[:-4]
    return "".join(c for c in raw if c in "0123456789abcdef")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/loaders/") or parsed.path.rstrip("/").endswith("/loader"):
            return self._serve_loader(parsed.path, parsed.query)
        self._json(200, {"name": "fine", "version": __version__, "status": "ok",
                         "storage": "postgres" if fine_store.enabled() else "ephemeral"})

    def _serve_loader(self, path, query):
        loader_hash = _loader_hash(path, query)
        if not loader_hash:
            return self._text(404, "-- not found")
        try:
            record = fine_store.load_loader(loader_hash)
        except Exception as e:
            return self._text(502, "-- storage error: " + str(e))
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

        action = payload.get("action")
        if action and action != "obfuscate":
            return self._manage(action, payload)
        return self._obfuscate(payload)

    def _obfuscate(self, payload):
        source = payload.get("source")
        if not isinstance(source, str) or not source.strip():
            return self._json(400, {"error": "missing 'source'"})
        options = payload.get("options") or {}
        name = options.get("name") or payload.get("name") or "script"
        try:
            output = _build(source, {**options, "name": name})
        except SyntaxError as e:
            return self._json(400, {"error": "syntax error: " + str(e)})
        except Exception as e:
            return self._json(500, {"error": str(e)})

        loader_hash = hashlib.md5(secrets.token_bytes(16)).hexdigest()
        script_id = secrets.token_urlsafe(24)
        try:
            fine_store.save_loader(loader_hash, script_id, output, name)
        except Exception as e:
            return self._json(502, {"error": "storage unavailable: " + str(e)})

        url = _base(self) + "/loaders/" + loader_hash + ".lua"
        self._json(200, {
            "name": name,
            "url": url,
            "loadstring": _loadstring(url),
            "script_id": script_id,
            "output": output,
            "bytes": len(output),
            "ephemeral_storage": not fine_store.enabled(),
        })

    def _manage(self, action, payload):
        script_id = payload.get("script_id")
        if not isinstance(script_id, str) or not script_id:
            return self._json(400, {"error": "missing 'script_id'"})
        loader_hash, record = fine_store.resolve_sid(script_id)
        if not record:
            return self._json(404, {"error": "unknown or expired script_id"})
        url = _base(self) + "/loaders/" + loader_hash + ".lua"

        if action == "info":
            return self._json(200, {
                "name": record.get("name"), "url": url,
                "loadstring": _loadstring(url), "frozen": record.get("frozen", False),
                "created": record.get("created"), "updated": record.get("updated"),
                "bytes": len(record.get("code") or ""),
            })
        if action == "delete":
            fine_store.remove(loader_hash, script_id)
            return self._json(200, {"deleted": True})
        if action in ("freeze", "unfreeze"):
            fine_store.set_frozen(loader_hash, record, action == "freeze")
            return self._json(200, {"frozen": action == "freeze", "url": url})
        if action == "update":
            source = payload.get("source")
            if not isinstance(source, str) or not source.strip():
                return self._json(400, {"error": "missing 'source'"})
            options = payload.get("options") or {}
            name = options.get("name") or record.get("name") or "script"
            try:
                output = _build(source, {**options, "name": name})
            except SyntaxError as e:
                return self._json(400, {"error": "syntax error: " + str(e)})
            except Exception as e:
                return self._json(500, {"error": str(e)})
            record["name"] = name
            fine_store.update_code(loader_hash, record, output)
            return self._json(200, {
                "updated": True, "name": name, "url": url,
                "loadstring": _loadstring(url), "output": output, "bytes": len(output),
            })
        return self._json(400, {"error": "unknown action: " + str(action)})

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
