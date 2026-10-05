"""SQLite state store.

One in-memory connection, used only from the event-loop thread. Every handler does its
reads and writes in one synchronous block with no `await` inside, and every write that
checks before it acts runs in one BEGIN IMMEDIATE transaction, so concurrent requests
are serialised and a check cannot be overtaken by another writer.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3

SCHEMA = """
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    email_norm TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL
);
CREATE TABLE tokens (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL
);
CREATE TABLE restaurants (
    id TEXT PRIMARY KEY,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    timezone TEXT NOT NULL,
    slot_minutes INTEGER NOT NULL,
    reservation_duration_minutes INTEGER NOT NULL,
    cancellation_cutoff_minutes INTEGER NOT NULL,
    opening_hours TEXT NOT NULL
);
CREATE TABLE dining_tables (
    restaurant_id TEXT NOT NULL,
    id TEXT NOT NULL,
    position INTEGER NOT NULL,
    label TEXT NOT NULL,
    capacity INTEGER NOT NULL,
    PRIMARY KEY (restaurant_id, id)
);
CREATE TABLE reservations (
    id TEXT PRIMARY KEY,
    reference TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    restaurant_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    party_size INTEGER NOT NULL,
    status TEXT NOT NULL,
    starts_at_local TEXT NOT NULL,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX reservations_occupancy ON reservations (restaurant_id, table_id, status, start_ts);
CREATE INDEX reservations_user ON reservations (user_id);
CREATE TABLE idempotency (
    user_id TEXT NOT NULL,
    path TEXT NOT NULL,
    key TEXT NOT NULL,
    request TEXT NOT NULL,
    status INTEGER NOT NULL,
    response TEXT NOT NULL,
    PRIMARY KEY (user_id, path, key)
);
"""

TABLES = ("users", "tokens", "restaurants", "dining_tables", "reservations", "idempotency")

conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
conn.row_factory = sqlite3.Row
conn.executescript(SCHEMA)


@contextlib.contextmanager
def tx():
    """One serialised write transaction; rolled back on any exception."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def restaurant_row(row: sqlite3.Row) -> dict:
    r = dict(row)
    r["opening_hours"] = json.loads(r["opening_hours"])
    return r


def get_restaurant(restaurant_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM restaurants WHERE id = ?", (restaurant_id,)).fetchone()
    return restaurant_row(row) if row else None


def get_tables(restaurant_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM dining_tables WHERE restaurant_id = ? ORDER BY position",
                        (restaurant_id,)).fetchall()
    out = []
    for row in rows:
        t = dict(row)
        t["label"] = json.loads(t["label"])
        out.append(t)
    return out


def get_table(restaurant_id: str, table_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM dining_tables WHERE restaurant_id = ? AND id = ?",
                       (restaurant_id, table_id)).fetchone()
    return dict(row) if row else None


def overlapping(restaurant_id: str, table_id: str, start_ts: int, end_ts: int,
                exclude_ids: tuple | list = ()) -> bool:
    """Is a confirmed reservation on this table overlapping [start, end)?"""
    sql = ("SELECT id FROM reservations WHERE restaurant_id = ? AND table_id = ? "
           "AND status = 'confirmed' AND start_ts < ? AND end_ts > ?")
    for row in conn.execute(sql, (restaurant_id, table_id, end_ts, start_ts)):
        if row["id"] not in exclude_ids:
            return True
    return False


def wipe() -> None:
    for t in TABLES:
        conn.execute(f"DELETE FROM {t}")
