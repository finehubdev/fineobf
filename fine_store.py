import json
import os
import time
import urllib.request

_URL = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
_TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")

_MEM = {}


def enabled():
    return bool(_URL and _TOKEN)


def _cmd(command):
    body = json.dumps(command).encode()
    req = urllib.request.Request(
        _URL,
        data=body,
        headers={
            "Authorization": "Bearer " + _TOKEN,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read() or b"{}").get("result")


def get(key):
    if enabled():
        raw = _cmd(["GET", key])
        return json.loads(raw) if raw else None
    return _MEM.get(key)


def put(key, value):
    if enabled():
        _cmd(["SET", key, json.dumps(value)])
    else:
        _MEM[key] = value


def delete(key):
    if enabled():
        _cmd(["DEL", key])
    else:
        _MEM.pop(key, None)


def save_loader(loader_hash, script_id, code, name):
    record = {
        "code": code,
        "sid": script_id,
        "name": name,
        "frozen": False,
        "created": int(time.time()),
        "updated": int(time.time()),
    }
    put("loader:" + loader_hash, record)
    put("sid:" + script_id, loader_hash)
    return record


def load_loader(loader_hash):
    return get("loader:" + loader_hash)


def resolve_sid(script_id):
    loader_hash = get("sid:" + script_id)
    if not loader_hash:
        return None, None
    return loader_hash, get("loader:" + loader_hash)


def update_code(loader_hash, record, code):
    record["code"] = code
    record["updated"] = int(time.time())
    put("loader:" + loader_hash, record)
    return record


def set_frozen(loader_hash, record, frozen):
    record["frozen"] = bool(frozen)
    record["updated"] = int(time.time())
    put("loader:" + loader_hash, record)
    return record


def remove(loader_hash, script_id):
    delete("loader:" + loader_hash)
    delete("sid:" + script_id)
