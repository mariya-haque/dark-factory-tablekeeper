"""Export and import of the whole service state (spec §10).

The exported `state` is every store table, row for row and column for column, so
tokens (as hashes), password hashes, references, timestamps and idempotency receipts
come back verbatim. Import validates everything before it replaces anything.
"""
from __future__ import annotations

import json
import sqlite3

from . import store
from .errors import invalid
from .fixture import REFERENCE_RE, validate_opening_hours
from .timeutil import LOCAL_RE, valid_zone

TRACK = "tablekeeper"
FORMAT_VERSION = 1

S, I = str, int
COLUMNS: dict[str, dict[str, type]] = {
    "users": {"id": S, "email": S, "email_norm": S, "password_hash": S, "display_name": S},
    "tokens": {"token_hash": S, "user_id": S},
    "restaurants": {"id": S, "position": I, "name": S, "timezone": S, "slot_minutes": I,
                    "reservation_duration_minutes": I, "cancellation_cutoff_minutes": I,
                    "opening_hours": S},
    "dining_tables": {"restaurant_id": S, "id": S, "position": I, "label": S, "capacity": I},
    "reservations": {"id": S, "reference": S, "user_id": S, "restaurant_id": S, "table_id": S,
                     "party_size": I, "status": S, "starts_at_local": S, "start_ts": I,
                     "end_ts": I, "created_at": S},
    "idempotency": {"user_id": S, "path": S, "key": S, "request": S, "status": I, "response": S},
}
INT_LIMIT = 2 ** 62


def export_state() -> dict:
    """An atomic snapshot: one synchronous read of every table."""
    state = {}
    for table in COLUMNS:
        rows = store.conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        state[table] = [dict(r) for r in rows]
    return {"track": TRACK, "format_version": FORMAT_VERSION, "state": state}


def _rows(state: dict, table: str) -> list[dict]:
    rows = state.get(table)
    if not isinstance(rows, list):
        raise invalid(f"state.{table} must be a list")
    cols = COLUMNS[table]
    out = []
    for row in rows:
        if not isinstance(row, dict):
            raise invalid(f"state.{table} rows must be objects")
        clean = {}
        for col, typ in cols.items():
            v = row.get(col)
            if type(v) is not typ or (typ is int and abs(v) >= INT_LIMIT):
                raise invalid(f"state.{table}.{col} is missing or of the wrong type")
            clean[col] = v
        out.append(clean)
    return out


def _json(text: str, what: str):
    try:
        return json.loads(text)
    except ValueError:
        raise invalid(f"{what} is not valid JSON") from None


def validate_import(body: dict) -> dict[str, list[dict]]:
    if body.get("track") != TRACK:
        raise invalid("track must be \"tablekeeper\"")
    v = body.get("format_version")
    if type(v) is not int or v != FORMAT_VERSION:
        raise invalid("format_version must be 1")
    state = body.get("state")
    if not isinstance(state, dict):
        raise invalid("state must be an object")
    rows = {t: _rows(state, t) for t in COLUMNS}

    users = {u["id"] for u in rows["users"]}
    for u in rows["users"]:
        if u["email_norm"] != u["email"].lower() or not u["password_hash"].startswith("scrypt$"):
            raise invalid("state.users holds an invalid account")
    if any(t["user_id"] not in users for t in rows["tokens"]):
        raise invalid("state.tokens refers to an unknown user")
    restaurants = {}
    for r in rows["restaurants"]:
        if not valid_zone(r["timezone"]) or r["slot_minutes"] < 1 or r["reservation_duration_minutes"] < 1 \
                or r["cancellation_cutoff_minutes"] < 0:
            raise invalid("state.restaurants holds an invalid restaurant")
        validate_opening_hours(_json(r["opening_hours"], "opening_hours"), r["id"])
        restaurants[r["id"]] = r
    tables = set()
    for t in rows["dining_tables"]:
        if t["restaurant_id"] not in restaurants or t["capacity"] < 1:
            raise invalid("state.dining_tables holds an invalid table")
        _json(t["label"], "table label")
        tables.add((t["restaurant_id"], t["id"]))
    for x in rows["reservations"]:
        if x["user_id"] not in users or (x["restaurant_id"], x["table_id"]) not in tables \
                or x["status"] not in ("confirmed", "cancelled") or x["party_size"] < 1 \
                or not LOCAL_RE.match(x["starts_at_local"]) or x["end_ts"] <= x["start_ts"]                 or not REFERENCE_RE.match(x["reference"]):
            raise invalid("state.reservations holds an invalid reservation")
    for k in rows["idempotency"]:
        if k["user_id"] not in users:
            raise invalid("state.idempotency refers to an unknown user")
        _json(k["request"], "idempotency request")
        if not isinstance(_json(k["response"], "idempotency response"), dict):
            raise invalid("state.idempotency response must be an object")
    return rows


def replace_state(rows: dict[str, list[dict]]) -> None:
    """Wipe and load. Call inside store.tx(); a constraint failure rolls everything back."""
    store.wipe()
    try:
        for table, cols in COLUMNS.items():
            names = list(cols)
            sql = f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})"
            store.conn.executemany(sql, [tuple(r[c] for c in names) for r in rows[table]])
    except sqlite3.IntegrityError:
        raise invalid("state holds duplicate or conflicting records") from None
