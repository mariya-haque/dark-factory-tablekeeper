"""Reset fixture validation and loading (spec §3.3, §4)."""
from __future__ import annotations

import json
import re

from . import store
from .errors import invalid
from .timeutil import WEEKDAYS, now_rfc3339, parse_hhmm, parse_local, resolve, valid_zone

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")
REFERENCE_RE = re.compile(r"^[A-Z0-9]{6,12}$")  # §8: 6 to 12 characters of A-Z0-9
RFC3339_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$")
MAX_ID = 64


def is_int(v: object) -> bool:
    return type(v) is int


def need_id(v: object, what: str) -> str:
    if not isinstance(v, str) or not 0 < len(v) <= MAX_ID:
        raise invalid(f"{what} must be a string of 1 to {MAX_ID} characters")
    return v


def need_int(v: object, what: str, lo: int, hi: int) -> int:
    if not is_int(v) or not lo <= v <= hi:
        raise invalid(f"{what} must be an integer from {lo} to {hi}")
    return v


def need_list(body: dict, name: str) -> list:
    v = body.get(name, [])
    if v is None:
        return []
    if not isinstance(v, list):
        raise invalid(f"{name} must be a list")
    return v


def need_obj(v: object, what: str) -> dict:
    if not isinstance(v, dict):
        raise invalid(f"{what} must be an object")
    return v


def validate_opening_hours(hours: object, what: str) -> list[dict]:
    if not isinstance(hours, list):
        raise invalid(f"{what}.opening_hours must be a list")
    out = []
    for h in hours:
        h = need_obj(h, f"{what}.opening_hours[]")
        if h.get("weekday") not in WEEKDAYS:
            raise invalid(f"{what}: weekday must be one of {' '.join(WEEKDAYS)}")
        o, c = parse_hhmm(h.get("opens")), parse_hhmm(h.get("closes"))
        if o is None or c is None or c <= o:
            raise invalid(f"{what}: opens/closes must be HH:MM with closes later than opens")
        out.append({"weekday": h["weekday"], "opens": h["opens"], "closes": h["closes"]})
    return out


def validate(body: dict) -> dict:
    """Check the whole fixture before anything is replaced. Returns normalised rows."""
    users, emails = {}, set()
    for u in need_list(body, "users"):
        u = need_obj(u, "user")
        uid = need_id(u.get("id"), "user.id")
        email, password = u.get("email"), u.get("password")
        if not isinstance(email, str) or not EMAIL_RE.match(email):
            raise invalid("user.email must be local@domain")
        if not isinstance(password, str) or not password:
            raise invalid("user.password must be a non-empty string")
        name = u.get("display_name", "")
        if not isinstance(name, str):
            raise invalid("user.display_name must be a string")
        if uid in users or email.lower() in emails:
            raise invalid("duplicate user id or email")
        emails.add(email.lower())
        users[uid] = {"id": uid, "email": email, "password": password, "display_name": name}

    restaurants = {}
    for r in need_list(body, "restaurants"):
        r = need_obj(r, "restaurant")
        rid = need_id(r.get("id"), "restaurant.id")
        if rid in restaurants:
            raise invalid("duplicate restaurant id")
        name = r.get("name")
        if not isinstance(name, str):
            raise invalid("restaurant.name must be a string")
        if not valid_zone(r.get("timezone")):
            raise invalid("restaurant.timezone must be an IANA zone name")
        tables, seen = [], set()
        raw_tables = r.get("tables", [])
        if not isinstance(raw_tables, list):
            raise invalid("restaurant.tables must be a list")
        for t in raw_tables:
            t = need_obj(t, "table")
            tid = need_id(t.get("id"), "table.id")
            if tid in seen:
                raise invalid("duplicate table id")
            seen.add(tid)
            tables.append({"id": tid, "label": t.get("label", tid),
                           "capacity": need_int(t.get("capacity"), "table.capacity", 1, 1_000_000)})
        restaurants[rid] = {
            "id": rid, "name": name, "timezone": r["timezone"],
            "slot_minutes": need_int(r.get("slot_minutes"), "slot_minutes", 1, 1440),
            "reservation_duration_minutes": need_int(r.get("reservation_duration_minutes"),
                                                     "reservation_duration_minutes", 1, 1440),
            "cancellation_cutoff_minutes": need_int(r.get("cancellation_cutoff_minutes"),
                                                    "cancellation_cutoff_minutes", 0, 10_000_000),
            "opening_hours": validate_opening_hours(r.get("opening_hours", []), rid),
            "tables": tables,
        }

    reservations, refs = {}, set()
    created = now_rfc3339()
    for x in need_list(body, "reservations"):
        x = need_obj(x, "reservation")
        xid = need_id(x.get("id"), "reservation.id")
        ref = x.get("reference")
        if not isinstance(ref, str) or not REFERENCE_RE.match(ref):
            raise invalid("reservation.reference must be 6 to 12 characters of A-Z0-9")
        if xid in reservations or ref in refs:
            raise invalid("duplicate reservation id or reference")
        refs.add(ref)
        uid = x.get("user_id")
        if uid not in users:
            raise invalid("reservation.user_id is not a fixture user")
        rest = restaurants.get(x.get("restaurant_id"))
        if rest is None:
            raise invalid("reservation.restaurant_id is not a fixture restaurant")
        table = next((t for t in rest["tables"] if t["id"] == x.get("table_id")), None)
        if table is None:
            raise invalid("reservation.table_id is not a table of that restaurant")
        local = x.get("starts_at_local")
        if not isinstance(local, str):
            raise invalid("reservation.starts_at_local must be a string")
        naive = parse_local(local)
        start = resolve(naive, rest["timezone"])
        if start is None:
            raise invalid("reservation.starts_at_local does not exist in the restaurant's zone")
        party = need_int(x.get("party_size"), "reservation.party_size", 1, 1_000_000)
        status = x.get("status", "confirmed")
        if status not in ("confirmed", "cancelled"):
            raise invalid("reservation.status must be confirmed or cancelled")
        created_at = x.get("created_at", created)
        if not isinstance(created_at, str) or not RFC3339_RE.match(created_at):
            raise invalid("reservation.created_at must be RFC 3339 with an explicit offset")
        reservations[xid] = {
            "id": xid, "reference": ref, "user_id": uid, "restaurant_id": rest["id"],
            "table_id": table["id"], "party_size": party, "status": status,
            "starts_at_local": local, "start_ts": start,
            "end_ts": start + rest["reservation_duration_minutes"] * 60, "created_at": created_at,
        }
    # §1: two confirmed reservations never occupy one table at overlapping times.
    by_table: dict[tuple[str, str], list[dict]] = {}
    for x in reservations.values():
        if x["status"] == "confirmed":
            by_table.setdefault((x["restaurant_id"], x["table_id"]), []).append(x)
    for held in by_table.values():
        held.sort(key=lambda x: x["start_ts"])
        for a, b in zip(held, held[1:]):
            if b["start_ts"] < a["end_ts"]:
                raise invalid(f"seeded reservations {a['id']} and {b['id']} overlap on one table")
    return {"users": list(users.values()), "restaurants": list(restaurants.values()),
            "reservations": list(reservations.values())}


def load(fx: dict, hashes: dict[str, str]) -> None:
    """Replace all state with a validated fixture. Call inside store.tx()."""
    c = store.conn
    store.wipe()
    for u in fx["users"]:
        c.execute("INSERT INTO users (id, email, email_norm, password_hash, display_name) "
                  "VALUES (?, ?, ?, ?, ?)",
                  (u["id"], u["email"], u["email"].lower(), hashes[u["id"]], u["display_name"]))
    for pos, r in enumerate(fx["restaurants"]):
        c.execute("INSERT INTO restaurants VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (r["id"], pos, r["name"], r["timezone"], r["slot_minutes"],
                   r["reservation_duration_minutes"], r["cancellation_cutoff_minutes"],
                   json.dumps(r["opening_hours"])))
        for tpos, t in enumerate(r["tables"]):
            c.execute("INSERT INTO dining_tables VALUES (?, ?, ?, ?, ?)",
                      (r["id"], t["id"], tpos, json.dumps(t["label"]), t["capacity"]))
    for x in fx["reservations"]:
        c.execute("INSERT INTO reservations (id, reference, user_id, restaurant_id, table_id, "
                  "party_size, status, starts_at_local, start_ts, end_ts, created_at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (x["id"], x["reference"], x["user_id"], x["restaurant_id"], x["table_id"],
                   x["party_size"], x["status"], x["starts_at_local"], x["start_ts"], x["end_ts"],
                   x["created_at"]))
