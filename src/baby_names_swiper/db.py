"""SQLite connection helpers."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading
from typing import TYPE_CHECKING

from baby_names_swiper.config import DB_PATH

if TYPE_CHECKING:
    from collections.abc import Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS swipes (
    user        TEXT    NOT NULL,
    list_slug   TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    direction   INTEGER NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user, list_slug, name)
);

CREATE INDEX IF NOT EXISTS idx_swipes_user_list
    ON swipes(user, list_slug);

CREATE INDEX IF NOT EXISTS idx_swipes_list_name_liked
    ON swipes(list_slug, name) WHERE direction = 1;

CREATE INDEX IF NOT EXISTS idx_swipes_recent
    ON swipes(user, list_slug, created_at DESC);
"""

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path | None = None) -> None:
    """Initialise the database connection and schema."""
    global _conn  # noqa: PLW0603
    with _lock:
        path = db_path or DB_PATH
        _conn = _connect(path)
        _conn.executescript(_SCHEMA)


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    """Yield a serialised cursor (one writer at a time, fine for SQLite + WAL)."""
    if _conn is None:
        init_db()
    assert _conn is not None
    with _lock:
        cur = _conn.cursor()
        try:
            yield cur
        finally:
            cur.close()


def reset_for_tests(db_path: Path) -> None:
    """Reinitialise the global connection at a new path (test helper)."""
    global _conn  # noqa: PLW0603
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = _connect(db_path)
        _conn.executescript(_SCHEMA)
