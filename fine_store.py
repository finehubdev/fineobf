import os
import secrets
import time
from contextlib import contextmanager

try:
    import psycopg2
    import psycopg2.extras
except Exception:
    psycopg2 = None

_DSN = (os.environ.get("DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
        or os.environ.get("DATABASE_URL_UNPOOLED")
        or os.environ.get("POSTGRES_URL_NON_POOLING"))

_MEM = {"loaders": {}, "sids": {}, "keys": {}, "acl": {}}
_ready = False

HWID_RESET_COOLDOWN = 2 * 24 * 3600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fine_loaders (
    hash    TEXT PRIMARY KEY,
    sid     TEXT UNIQUE NOT NULL,
    code    TEXT NOT NULL,
    name    TEXT,
    frozen  BOOLEAN NOT NULL DEFAULT FALSE,
    created BIGINT,
    updated BIGINT
);
ALTER TABLE fine_loaders ADD COLUMN IF NOT EXISTS free  BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE fine_loaders ADD COLUMN IF NOT EXISTS owner TEXT;
CREATE TABLE IF NOT EXISTS fine_keys (
    key        TEXT PRIMARY KEY,
    project    TEXT NOT NULL,
    hwid       TEXT,
    hwid_reset BIGINT,
    discord_id TEXT,
    label      TEXT,
    created    BIGINT
);
CREATE INDEX IF NOT EXISTS fine_keys_project_idx ON fine_keys (project);
CREATE INDEX IF NOT EXISTS fine_keys_owner_idx   ON fine_keys (project, discord_id);
CREATE TABLE IF NOT EXISTS fine_acl (
    project    TEXT NOT NULL,
    discord_id TEXT NOT NULL,
    status     TEXT NOT NULL,
    PRIMARY KEY (project, discord_id)
);
"""


def enabled():
    return bool(_DSN and psycopg2)


@contextmanager
def _conn():
    global _ready
    conn = psycopg2.connect(_DSN, connect_timeout=10)
    conn.autocommit = True
    try:
        if not _ready:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
            _ready = True
        yield conn
    finally:
        conn.close()


def _dict_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def new_key():
    h = secrets.token_hex(10).upper()
    return "FINE-" + "-".join(h[i:i + 4] for i in range(0, 20, 4))


# ---------------- loaders / projects ----------------

def save_loader(loader_hash, script_id, code, name, owner=None, free=False):
    now = int(time.time())
    record = {"code": code, "sid": script_id, "name": name, "frozen": False,
              "free": bool(free), "owner": owner, "created": now, "updated": now}
    if enabled():
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO fine_loaders (hash, sid, code, name, frozen, free, owner, created, updated) "
                "VALUES (%s, %s, %s, %s, FALSE, %s, %s, %s, %s)",
                (loader_hash, script_id, code, name, bool(free), owner, now, now))
    else:
        _MEM["loaders"][loader_hash] = record
        _MEM["sids"][script_id] = loader_hash
    return record


def load_loader(loader_hash):
    if enabled():
        with _conn() as conn, _dict_cursor(conn) as cur:
            cur.execute("SELECT * FROM fine_loaders WHERE hash = %s", (loader_hash,))
            row = cur.fetchone()
            return dict(row) if row else None
    return _MEM["loaders"].get(loader_hash)


def resolve_sid(script_id):
    if enabled():
        with _conn() as conn, _dict_cursor(conn) as cur:
            cur.execute("SELECT * FROM fine_loaders WHERE sid = %s", (script_id,))
            row = cur.fetchone()
            if not row:
                return None, None
            return row["hash"], dict(row)
    loader_hash = _MEM["sids"].get(script_id)
    if not loader_hash:
        return None, None
    return loader_hash, _MEM["loaders"].get(loader_hash)


def update_code(loader_hash, record, code):
    now = int(time.time())
    if enabled():
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE fine_loaders SET code = %s, name = %s, updated = %s WHERE hash = %s",
                        (code, record.get("name"), now, loader_hash))
    else:
        record["code"] = code
        record["updated"] = now
        _MEM["loaders"][loader_hash] = record
    return record


def set_frozen(loader_hash, record, frozen):
    now = int(time.time())
    if enabled():
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE fine_loaders SET frozen = %s, updated = %s WHERE hash = %s",
                        (bool(frozen), now, loader_hash))
    else:
        record["frozen"] = bool(frozen)
        record["updated"] = now
        _MEM["loaders"][loader_hash] = record
    return record


def set_free(loader_hash, record, free):
    now = int(time.time())
    if enabled():
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE fine_loaders SET free = %s, updated = %s WHERE hash = %s",
                        (bool(free), now, loader_hash))
    else:
        record["free"] = bool(free)
        record["updated"] = now
        _MEM["loaders"][loader_hash] = record
    return record


def remove(loader_hash, script_id):
    if enabled():
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM fine_loaders WHERE hash = %s", (loader_hash,))
            cur.execute("DELETE FROM fine_keys WHERE project = %s", (loader_hash,))
            cur.execute("DELETE FROM fine_acl WHERE project = %s", (loader_hash,))
    else:
        _MEM["loaders"].pop(loader_hash, None)
        _MEM["sids"].pop(script_id, None)
        for k, v in list(_MEM["keys"].items()):
            if v.get("project") == loader_hash:
                _MEM["keys"].pop(k, None)
        _MEM["acl"].pop(loader_hash, None)


# ---------------- keys ----------------

def create_keys(project, count=1, label=None):
    now = int(time.time())
    out = []
    for _ in range(max(1, min(int(count), 100))):
        key = new_key()
        rec = {"key": key, "project": project, "hwid": None, "hwid_reset": None,
               "discord_id": None, "label": label, "created": now}
        if enabled():
            with _conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO fine_keys (key, project, label, created) VALUES (%s, %s, %s, %s)",
                    (key, project, label, now))
        else:
            _MEM["keys"][key] = rec
        out.append(key)
    return out


def get_key(key):
    if enabled():
        with _conn() as conn, _dict_cursor(conn) as cur:
            cur.execute("SELECT * FROM fine_keys WHERE key = %s", (key,))
            row = cur.fetchone()
            return dict(row) if row else None
    return _MEM["keys"].get(key)


def get_project_key(project, key):
    rec = get_key(key)
    if rec and rec.get("project") == project:
        return rec
    return None


def delete_key(project, key):
    if enabled():
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM fine_keys WHERE key = %s AND project = %s", (key, project))
            return cur.rowcount > 0
    rec = _MEM["keys"].get(key)
    if rec and rec.get("project") == project:
        _MEM["keys"].pop(key, None)
        return True
    return False


def list_keys(project):
    if enabled():
        with _conn() as conn, _dict_cursor(conn) as cur:
            cur.execute("SELECT * FROM fine_keys WHERE project = %s ORDER BY created DESC", (project,))
            return [dict(r) for r in cur.fetchall()]
    return [v for v in _MEM["keys"].values() if v.get("project") == project]


def key_for_discord(project, discord_id):
    if enabled():
        with _conn() as conn, _dict_cursor(conn) as cur:
            cur.execute("SELECT * FROM fine_keys WHERE project = %s AND discord_id = %s "
                        "ORDER BY created DESC LIMIT 1", (project, discord_id))
            row = cur.fetchone()
            return dict(row) if row else None
    for v in _MEM["keys"].values():
        if v.get("project") == project and v.get("discord_id") == discord_id:
            return v
    return None


def bind_discord(key, discord_id):
    if enabled():
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE fine_keys SET discord_id = %s WHERE key = %s", (discord_id, key))
    else:
        _MEM["keys"][key]["discord_id"] = discord_id


def assign_hwid(key, hwid):
    if enabled():
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE fine_keys SET hwid = %s WHERE key = %s", (hwid, key))
    else:
        _MEM["keys"][key]["hwid"] = hwid


def reset_hwid(key):
    now = int(time.time())
    if enabled():
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE fine_keys SET hwid = NULL, hwid_reset = %s WHERE key = %s", (now, key))
    else:
        _MEM["keys"][key]["hwid"] = None
        _MEM["keys"][key]["hwid_reset"] = now


def reset_available_in(record):
    last = record.get("hwid_reset")
    if not last:
        return 0
    remaining = (last + HWID_RESET_COOLDOWN) - int(time.time())
    return remaining if remaining > 0 else 0


# ---------------- acl (whitelist / blacklist) ----------------

def set_acl(project, discord_id, status):
    if status not in ("white", "black"):
        return
    if enabled():
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO fine_acl (project, discord_id, status) VALUES (%s, %s, %s) "
                "ON CONFLICT (project, discord_id) DO UPDATE SET status = EXCLUDED.status",
                (project, discord_id, status))
    else:
        _MEM["acl"].setdefault(project, {})[discord_id] = status


def clear_acl(project, discord_id):
    if enabled():
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM fine_acl WHERE project = %s AND discord_id = %s",
                        (project, discord_id))
    else:
        _MEM["acl"].get(project, {}).pop(discord_id, None)


def get_acl(project, discord_id):
    if enabled():
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT status FROM fine_acl WHERE project = %s AND discord_id = %s",
                        (project, discord_id))
            row = cur.fetchone()
            return row[0] if row else None
    return _MEM["acl"].get(project, {}).get(discord_id)


def list_acl(project):
    if enabled():
        with _conn() as conn, _dict_cursor(conn) as cur:
            cur.execute("SELECT discord_id, status FROM fine_acl WHERE project = %s", (project,))
            return [dict(r) for r in cur.fetchall()]
    return [{"discord_id": d, "status": s} for d, s in _MEM["acl"].get(project, {}).items()]
