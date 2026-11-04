import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from app.config import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER,
    CHECK (end_ts IS NULL OR end_ts > start_ts)
);
CREATE INDEX IF NOT EXISTS entries_start ON entries (start_ts);
CREATE UNIQUE INDEX IF NOT EXISTS one_running ON entries ((1)) WHERE end_ts IS NULL;
CREATE TABLE IF NOT EXISTS absences (
    day TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('urlaub', 'krank')),
    fraction REAL NOT NULL CHECK (fraction IN (0.5, 1.0))
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    created_ts INTEGER NOT NULL,
    expires_ts INTEGER NOT NULL
);
"""

_initialized: set[str] = set()


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    if str(path) not in _initialized:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        _initialized.add(str(path))
    return conn


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_tx(conn: sqlite3.Connection) -> Iterator[None]:
    """Serialised write transaction, so overlap checks and inserts cannot race."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
