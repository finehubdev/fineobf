import os
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

_MEM = {}
_ready = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fine_loaders (
    hash    TEXT PRIMARY KEY,
    sid     TEXT UNIQUE NOT NULL,
    code    TEXT NOT NULL,
    name    TEXT,
    frozen  BOOLEAN NOT NULL DEFAULT FALSE,
    created BIGINT,
    updated BIGINT
)
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


def save_loader(loader_hash, script_id, code, name):
    now = int(time.time())
    record = {"code": code, "sid": script_id, "name": name,
              "frozen": False, "created": now, "updated": now}
    if enabled():
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO fine_loaders (hash, sid, code, name, frozen, created, updated) "
                "VALUES (%s, %s, %s, %s, FALSE, %s, %s)",
                (loader_hash, script_id, code, name, now, now))
    else:
        _MEM["loader:" + loader_hash] = record
        _MEM["sid:" + script_id] = loader_hash
    return record


def load_loader(loader_hash):
    if enabled():
        with _conn() as conn, _dict_cursor(conn) as cur:
            cur.execute("SELECT * FROM fine_loaders WHERE hash = %s", (loader_hash,))
            row = cur.fetchone()
            return dict(row) if row else None
    return _MEM.get("loader:" + loader_hash)


def resolve_sid(script_id):
    if enabled():
        with _conn() as conn, _dict_cursor(conn) as cur:
            cur.execute("SELECT * FROM fine_loaders WHERE sid = %s", (script_id,))
            row = cur.fetchone()
            if not row:
                return None, None
            return row["hash"], dict(row)
    loader_hash = _MEM.get("sid:" + script_id)
    if not loader_hash:
        return None, None
    return loader_hash, _MEM.get("loader:" + loader_hash)


def update_code(loader_hash, record, code):
    now = int(time.time())
    if enabled():
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE fine_loaders SET code = %s, name = %s, updated = %s WHERE hash = %s",
                        (code, record.get("name"), now, loader_hash))
    else:
        record["code"] = code
        record["updated"] = now
        _MEM["loader:" + loader_hash] = record
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
        _MEM["loader:" + loader_hash] = record
    return record


def remove(loader_hash, script_id):
    if enabled():
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM fine_loaders WHERE hash = %s", (loader_hash,))
    else:
        _MEM.pop("loader:" + loader_hash, None)
        _MEM.pop("sid:" + script_id, None)
