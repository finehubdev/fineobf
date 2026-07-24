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
_BOT_SECRET = os.environ.get("BOT_SHARED_SECRET")

OWNER_ACTIONS = {"update", "freeze", "unfreeze", "delete", "info", "set_free",
                 "genkey", "delkey", "listkeys", "whitelist", "blacklist",
                 "unlist", "listacl"}
PANEL_ACTIONS = {"panel_info", "redeem", "get_script", "reset_hwid",
                 "buyer_check", "key_info"}

_GATEWAY = r"""local ok=pcall(function()
    local HS=game:GetService("HttpService")
    local key=""
    pcall(function() if script_key~=nil then key=tostring(script_key) end end)
    if key=="" and getgenv then pcall(function() local g=getgenv().script_key if g~=nil then key=tostring(g) end end) end
    local hwid=""
    pcall(function() hwid=tostring(game:GetService("RbxAnalyticsService"):GetClientId()) end)
    if hwid=="" and gethwid then pcall(function() hwid=tostring(gethwid()) end) end
    local url="__BASE__/verify/__HASH__?key="..HS:UrlEncode(key).."&hwid="..HS:UrlEncode(hwid)
    local body=game:HttpGet(url)
    local f=loadstring(body)
    if f then f() end
end)"""


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


def _gateway(base, loader_hash):
    return _GATEWAY.replace("__BASE__", base).replace("__HASH__", loader_hash)


def _kick(msg):
    safe = msg.replace("\\", "\\\\").replace('"', '\\"')
    return 'local p=game:GetService("Players").LocalPlayer;if p then p:Kick("' + safe + '")end'


def _hex_hash(path, query):
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
        p = parsed.path
        if p.startswith("/verify/"):
            return self._serve_verify(p, parsed.query)
        if p.startswith("/loaders/") or p.rstrip("/").endswith("/loader"):
            return self._serve_loader(p, parsed.query)
        self._json(200, {"name": "fine", "version": __version__, "status": "ok",
                         "storage": "postgres" if fine_store.enabled() else "ephemeral"})

    def _serve_loader(self, path, query):
        loader_hash = _hex_hash(path, query)
        if not loader_hash:
            return self._text(404, "-- not found")
        try:
            record = fine_store.load_loader(loader_hash)
        except Exception as e:
            return self._text(502, "-- storage error: " + str(e))
        if not record:
            return self._text(404, "-- not found")
        if record.get("frozen"):
            return self._text(200, _kick("Fineobf: Script Frozen"))
        self._text(200, _gateway(_base(self), loader_hash))

    def _serve_verify(self, path, query):
        loader_hash = _hex_hash(path, query)
        q = parse_qs(query)
        key = (q.get("key") or [""])[0].strip()
        hwid = (q.get("hwid") or [""])[0].strip()
        try:
            record = fine_store.load_loader(loader_hash)
        except Exception as e:
            return self._text(200, _kick("Fineobf: Server Error"))
        if not record:
            return self._text(200, _kick("Fineobf: Invalid Script"))
        if record.get("frozen"):
            return self._text(200, _kick("Fineobf: Script Frozen"))
        if record.get("free"):
            return self._text(200, record.get("code") or "")
        if not key:
            return self._text(200, _kick("Fineobf: No Key Provided."))
        krec = fine_store.get_project_key(loader_hash, key)
        if not krec:
            return self._text(200, _kick("Fineobf: Invalid Key."))
        if krec.get("discord_id") and fine_store.get_acl(loader_hash, krec["discord_id"]) == "black":
            return self._text(200, _kick("Fineobf: Access Revoked."))
        if not hwid:
            return self._text(200, _kick("Fineobf: HWID Unavailable."))
        if not krec.get("hwid"):
            fine_store.assign_hwid(key, hwid)
        elif krec.get("hwid") != hwid:
            return self._text(200, _kick("Fineobf: HWID Mismatch"))
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
        if action in PANEL_ACTIONS:
            return self._panel(action, payload)
        if action in OWNER_ACTIONS:
            return self._manage(action, payload)
        if action and action != "obfuscate":
            return self._json(400, {"error": "unknown action: " + str(action)})
        return self._obfuscate(payload)

    def _obfuscate(self, payload):
        source = payload.get("source")
        if not isinstance(source, str) or not source.strip():
            return self._json(400, {"error": "missing 'source'"})
        options = payload.get("options") or {}
        name = options.get("name") or payload.get("name") or "script"
        free = bool(options.get("free") if options.get("free") is not None else payload.get("free"))
        owner = payload.get("owner")
        try:
            output = _build(source, {**options, "name": name})
        except SyntaxError as e:
            return self._json(400, {"error": "syntax error: " + str(e)})
        except Exception as e:
            return self._json(500, {"error": str(e)})

        loader_hash = hashlib.md5(secrets.token_bytes(16)).hexdigest()
        script_id = secrets.token_urlsafe(24)
        try:
            fine_store.save_loader(loader_hash, script_id, output, name, owner=owner, free=free)
        except Exception as e:
            return self._json(502, {"error": "storage unavailable: " + str(e)})

        url = _base(self) + "/loaders/" + loader_hash + ".lua"
        self._json(200, {
            "name": name,
            "project_id": loader_hash,
            "url": url,
            "loadstring": _loadstring(url),
            "script_id": script_id,
            "free": free,
            "output": output,
            "bytes": len(output),
            "ephemeral_storage": not fine_store.enabled(),
        })

    # ---------------- owner management (script_id) ----------------

    def _manage(self, action, payload):
        script_id = payload.get("script_id")
        if not isinstance(script_id, str) or not script_id:
            return self._json(400, {"error": "missing 'script_id'"})
        loader_hash, record = fine_store.resolve_sid(script_id)
        if not record:
            return self._json(404, {"error": "unknown or expired script_id"})
        url = _base(self) + "/loaders/" + loader_hash + ".lua"

        if action == "info":
            keys = fine_store.list_keys(loader_hash)
            return self._json(200, {
                "name": record.get("name"), "project_id": loader_hash, "url": url,
                "loadstring": _loadstring(url), "frozen": record.get("frozen", False),
                "free": record.get("free", False), "keys": len(keys),
                "created": record.get("created"), "updated": record.get("updated"),
                "bytes": len(record.get("code") or ""),
            })
        if action == "delete":
            fine_store.remove(loader_hash, script_id)
            return self._json(200, {"deleted": True})
        if action in ("freeze", "unfreeze"):
            fine_store.set_frozen(loader_hash, record, action == "freeze")
            return self._json(200, {"frozen": action == "freeze", "url": url})
        if action == "set_free":
            free = bool(payload.get("free"))
            fine_store.set_free(loader_hash, record, free)
            return self._json(200, {"free": free})
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
        if action == "genkey":
            count = payload.get("count", 1)
            label = payload.get("label")
            keys = fine_store.create_keys(loader_hash, count, label)
            return self._json(200, {"keys": keys, "count": len(keys)})
        if action == "delkey":
            key = payload.get("key")
            if not key:
                return self._json(400, {"error": "missing 'key'"})
            ok = fine_store.delete_key(loader_hash, key)
            return self._json(200, {"deleted": ok})
        if action == "listkeys":
            keys = fine_store.list_keys(loader_hash)
            return self._json(200, {"keys": [{
                "key": k.get("key"), "hwid_bound": bool(k.get("hwid")),
                "discord_id": k.get("discord_id"), "label": k.get("label"),
                "created": k.get("created")} for k in keys]})
        if action in ("whitelist", "blacklist"):
            did = str(payload.get("discord_id") or "")
            if not did:
                return self._json(400, {"error": "missing 'discord_id'"})
            fine_store.set_acl(loader_hash, did, "white" if action == "whitelist" else "black")
            return self._json(200, {"discord_id": did, "status": "white" if action == "whitelist" else "black"})
        if action == "unlist":
            did = str(payload.get("discord_id") or "")
            fine_store.clear_acl(loader_hash, did)
            return self._json(200, {"discord_id": did, "status": "cleared"})
        if action == "listacl":
            return self._json(200, {"acl": fine_store.list_acl(loader_hash)})
        return self._json(400, {"error": "unknown action: " + str(action)})

    # ---------------- public panel (project id + discord id) ----------------

    def _panel(self, action, payload):
        if _BOT_SECRET and self.headers.get("x-fine-auth") != _BOT_SECRET:
            return self._json(401, {"error": "unauthorized"})
        project = payload.get("project")
        if not isinstance(project, str) or not project:
            return self._json(400, {"error": "missing 'project'"})
        record = fine_store.load_loader(project)
        if not record:
            return self._json(404, {"error": "unknown project"})
        url = _base(self) + "/loaders/" + project + ".lua"

        if action == "panel_info":
            return self._json(200, {"name": record.get("name"), "free": record.get("free", False),
                                    "frozen": record.get("frozen", False)})

        did = str(payload.get("discord_id") or "")
        if not did:
            return self._json(400, {"error": "missing 'discord_id'"})
        if fine_store.get_acl(project, did) == "black":
            return self._json(403, {"error": "You are blacklisted from this script."})

        if action == "redeem":
            key = (payload.get("key") or "").strip()
            if not key:
                return self._json(400, {"error": "missing 'key'"})
            krec = fine_store.get_project_key(project, key)
            if not krec:
                return self._json(404, {"error": "Invalid key for this script."})
            if krec.get("discord_id") and krec["discord_id"] != did:
                return self._json(409, {"error": "That key was already redeemed by someone else."})
            fine_store.bind_discord(key, did)
            return self._json(200, {"redeemed": True, "key": key})

        if action == "get_script":
            krec = fine_store.key_for_discord(project, did)
            if not krec and not record.get("free"):
                return self._json(404, {"error": "Redeem a key first."})
            key = krec.get("key") if krec else None
            if key:
                full = 'script_key="' + key + '"\n' + _loadstring(url)
            else:
                full = _loadstring(url)
            return self._json(200, {"loadstring": full, "key": key, "url": url})

        if action == "reset_hwid":
            krec = fine_store.key_for_discord(project, did)
            if not krec:
                return self._json(404, {"error": "You have no redeemed key to reset."})
            remaining = fine_store.reset_available_in(krec)
            if remaining > 0:
                return self._json(429, {"error": "HWID reset on cooldown.", "reset_in": remaining})
            fine_store.reset_hwid(krec["key"])
            return self._json(200, {"reset": True})

        if action == "buyer_check":
            krec = fine_store.key_for_discord(project, did)
            eligible = bool(krec) or bool(record.get("free"))
            return self._json(200, {"eligible": eligible})

        if action == "key_info":
            krec = fine_store.key_for_discord(project, did)
            if not krec:
                return self._json(200, {"redeemed": False, "free": record.get("free", False)})
            return self._json(200, {
                "redeemed": True, "key": krec.get("key"),
                "hwid_bound": bool(krec.get("hwid")),
                "reset_in": fine_store.reset_available_in(krec),
                "free": record.get("free", False),
            })
        return self._json(400, {"error": "unknown action: " + str(action)})

    def _cors(self):
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
        self.send_header("access-control-allow-headers", "content-type, x-fine-auth")

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
