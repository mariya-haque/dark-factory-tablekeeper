"""HTTP API (spec §3, §6, §7, §8)."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from . import fixture, security, store, transfer
from .errors import ApiError, JSONUtf8Response, error_response, invalid, malformed, not_found
from .timeutil import check_start, day_slots, format_local, now_rfc3339, now_ts, parse_date, parse_local, rfc3339, zone

log = logging.getLogger("tablekeeper")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None,
              default_response_class=JSONUtf8Response)

# Password hashing runs here, off the event loop.
hash_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hash")

REFERENCE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
DIGITS_RE = re.compile(r"^[0-9]+$")
MISSING = object()


# ------------------------------------------------------------------ errors

@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    return error_response(exc.status, exc.code, exc.message)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    codes = {404: "not_found", 405: "method_not_allowed", 401: "unauthenticated",
             403: "forbidden", 400: "malformed_request", 422: "validation_failed"}
    code = codes.get(exc.status_code, "error" if exc.status_code < 500 else "internal_error")
    resp = error_response(exc.status_code, code, str(exc.detail))
    if exc.headers:
        resp.headers.update(exc.headers)
    return resp


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return error_response(422, "validation_failed", "request validation failed")


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return error_response(500, "internal_error", "internal server error")


# ------------------------------------------------------------------ helpers

def ok(content, status: int = 200) -> JSONUtf8Response:
    return JSONUtf8Response(content, status_code=status)


async def json_object(request: Request) -> dict:
    raw = await request.body()
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise malformed("request body is not valid JSON") from None
    if not isinstance(body, dict):
        raise malformed("request body must be a JSON object")
    return body


def authenticate(request: Request) -> dict:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token or " " in token:
        raise ApiError(401, "unauthenticated", "a valid bearer token is required")
    row = store.conn.execute(
        "SELECT u.* FROM tokens t JOIN users u ON u.id = t.user_id WHERE t.token_hash = ?",
        (security.token_hash(token),)).fetchone()
    if row is None:
        raise ApiError(401, "unauthenticated", "a valid bearer token is required")
    return dict(row)


def idempotency_key(request: Request) -> str:
    k = request.headers.get("idempotency-key")
    if k is None or k == "":
        raise ApiError(400, "missing_idempotency_key", "Idempotency-Key header is required")
    if len(k) > 255:
        raise invalid("Idempotency-Key must be 1 to 255 characters")
    return k


def canonical(body) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def find_receipt(user_id: str, path: str, key: str, request_text: str) -> JSONUtf8Response | None:
    """Replay (200, original body), reuse (409) or None for a first use."""
    row = store.conn.execute("SELECT request, response FROM idempotency "
                             "WHERE user_id = ? AND path = ? AND key = ?",
                             (user_id, path, key)).fetchone()
    if row is None:
        return None
    if row["request"] != request_text:
        raise ApiError(409, "idempotency_key_reuse", "this Idempotency-Key was used with a different body")
    return ok(json.loads(row["response"]), 200)


def store_receipt(user_id: str, path: str, key: str, request_text: str, status: int, response: dict) -> None:
    store.conn.execute("INSERT INTO idempotency (user_id, path, key, request, status, response) "
                       "VALUES (?, ?, ?, ?, ?, ?)",
                       (user_id, path, key, request_text, status, canonical(response)))


def reservation_out(row) -> dict:
    tz = row["tz"]
    return {
        "reservation_id": row["id"],
        "reference": row["reference"],
        "restaurant_id": row["restaurant_id"],
        "table_id": row["table_id"],
        "party_size": row["party_size"],
        "status": row["status"],
        "starts_at_local": row["starts_at_local"],
        "starts_at": rfc3339(row["start_ts"], tz),
        "ends_at": rfc3339(row["end_ts"], tz),
        "created_at": row["created_at"],
    }


RES_SELECT = ("SELECT r.*, x.timezone AS tz FROM reservations r "
              "JOIN restaurants x ON x.id = r.restaurant_id ")


def own_reservation(user_id: str, reference: str) -> dict:
    row = store.conn.execute(RES_SELECT + "WHERE r.reference = ? AND r.user_id = ?",
                             (reference, user_id)).fetchone()
    if row is None:
        raise not_found("no such reservation")
    return dict(row)


def reservation_by_id(res_id: str) -> dict:
    return dict(store.conn.execute(RES_SELECT + "WHERE r.id = ?", (res_id,)).fetchone())


def check_party(v) -> int:
    # bool is a subclass of int in Python and must be rejected explicitly.
    if type(v) is not int or v < 1:
        raise invalid("party_size must be an integer of at least 1")
    return v


def new_reference() -> str:
    while True:
        ref = "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(8))
        if store.conn.execute("SELECT 1 FROM reservations WHERE reference = ?", (ref,)).fetchone() is None:
            return ref


def new_id(prefix: str, table: str) -> str:
    while True:
        i = prefix + secrets.token_hex(8)
        if store.conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (i,)).fetchone() is None:
            return i


def check_cutoff(row: dict, restaurant: dict) -> None:
    """No cancel or change from `cutoff` minutes before the current start onwards."""
    if now_ts() >= row["start_ts"] - restaurant["cancellation_cutoff_minutes"] * 60:
        raise ApiError(409, "cutoff_passed", "the cancellation cutoff has passed")


def plan_amendment(row: dict, restaurant: dict, change: dict, cutoff_first: bool) -> dict:
    """Validate a PATCH-style change to one booking; raise its first non-occupancy error.

    Precedence: cancelled, then wrong JSON types, field values, unknown table, time
    rules and capacity, with the cutoff (on the current start) either before them
    (batch moves, §11) or after them (PATCH: "validation is identical to POST, and the
    same cutoff rule as cancel applies"). Occupancy is checked by the caller.
    """
    if row["status"] == "cancelled":
        raise ApiError(409, "reservation_cancelled", "the reservation is cancelled")
    if cutoff_first:
        check_cutoff(row, restaurant)
    table_id = change.get("table_id", MISSING)
    local = change.get("starts_at_local", MISSING)
    party = change.get("party_size", MISSING)
    if table_id is not MISSING and not isinstance(table_id, str):
        raise malformed("table_id must be a string")
    if local is not MISSING and not isinstance(local, str):
        raise malformed("starts_at_local must be a string")
    if party is not MISSING:
        check_party(party)
    naive = parse_local(local) if local is not MISSING else None

    new = {"table_id": row["table_id"], "starts_at_local": row["starts_at_local"],
           "start_ts": row["start_ts"], "end_ts": row["end_ts"], "party_size": row["party_size"]}
    table = None
    if table_id is not MISSING:
        table = store.get_table(restaurant["id"], table_id)
        if table is None:
            raise not_found("no such table at this restaurant")
        new["table_id"] = table_id
    if naive is not None:
        new["start_ts"], new["end_ts"] = check_start(restaurant, naive)
        new["starts_at_local"] = format_local(naive)
    if party is not MISSING:
        new["party_size"] = party
    if table_id is not MISSING or party is not MISSING:
        if table is None:
            table = store.get_table(restaurant["id"], new["table_id"])
        if table is not None and new["party_size"] > table["capacity"]:
            raise ApiError(422, "party_exceeds_capacity", "party is larger than the table's capacity")
    if not cutoff_first:
        check_cutoff(row, restaurant)
    new["moves"] = (new["table_id"] != row["table_id"] or new["start_ts"] != row["start_ts"]
                    or new["end_ts"] != row["end_ts"])
    return new


def apply_amendment(row: dict, new: dict) -> None:
    store.conn.execute("UPDATE reservations SET table_id = ?, starts_at_local = ?, start_ts = ?, "
                       "end_ts = ?, party_size = ? WHERE id = ?",
                       (new["table_id"], new["starts_at_local"], new["start_ts"], new["end_ts"],
                        new["party_size"], row["id"]))


# ------------------------------------------------------------------ runtime

@app.get("/health")
async def health():
    store.conn.execute("SELECT 1").fetchone()
    zone("Europe/Berlin")
    return ok({"status": "ok"})


@app.post("/_test/reset")
async def reset(request: Request):
    body = await json_object(request)
    fx = fixture.validate(body)
    loop = asyncio.get_running_loop()
    hashed = await asyncio.gather(*(
        loop.run_in_executor(hash_pool, security.hash_fixture_password, u["id"], u["password"])
        for u in fx["users"]))
    hashes = {u["id"]: h for u, h in zip(fx["users"], hashed)}
    with store.tx():
        fixture.load(fx, hashes)
    return Response(status_code=204)


# ------------------------------------------------------------------ auth

def credentials(body: dict, signup: bool) -> tuple[str, str, str | None]:
    fields = ("email", "password", "display_name") if signup else ("email", "password")
    for f in fields:
        if f in body and not isinstance(body[f], str):
            raise malformed(f"{f} must be a string")
    for f in fields:
        if f not in body:
            raise invalid(f"{f} is required")
    email, password = body["email"], body["password"]
    if signup:
        if not fixture.EMAIL_RE.match(email) or len(email) > 254:
            raise invalid("email must be of the form local@domain")
        if len(password) < 8:
            raise invalid("password must be at least 8 characters")
        if len(password) > 1024:
            raise invalid("password must be at most 1024 characters")
        if len(body["display_name"]) > 200:
            raise invalid("display_name must be at most 200 characters")
    return email, password, body.get("display_name")


def issue_token(user_id: str) -> str:
    token = security.new_token()
    store.conn.execute("INSERT INTO tokens (token_hash, user_id) VALUES (?, ?)",
                       (security.token_hash(token), user_id))
    return token


@app.post("/auth/signup")
async def signup(request: Request):
    body = await json_object(request)
    email, password, name = credentials(body, signup=True)
    taken = "SELECT 1 FROM users WHERE email_norm = ?"
    if store.conn.execute(taken, (email.lower(),)).fetchone():
        raise ApiError(409, "email_taken", "that email is already registered")
    pw_hash = await asyncio.get_running_loop().run_in_executor(hash_pool, security.hash_password, password)
    with store.tx():
        if store.conn.execute(taken, (email.lower(),)).fetchone():
            raise ApiError(409, "email_taken", "that email is already registered")
        uid = new_id("u_", "users")
        store.conn.execute("INSERT INTO users (id, email, email_norm, password_hash, display_name) "
                           "VALUES (?, ?, ?, ?, ?)", (uid, email, email.lower(), pw_hash, name))
        token = issue_token(uid)
    return ok({"user_id": uid, "display_name": name, "token": token}, 201)


@app.post("/auth/login")
async def login(request: Request):
    body = await json_object(request)
    email, password, _ = credentials(body, signup=False)
    row = store.conn.execute("SELECT * FROM users WHERE email_norm = ?", (email.lower(),)).fetchone()
    loop = asyncio.get_running_loop()
    if row is None:
        await loop.run_in_executor(hash_pool, security.burn_verify)
        raise ApiError(401, "unauthenticated", "wrong email or password")
    user = dict(row)
    good = await loop.run_in_executor(hash_pool, security.verify_password, password, user["password_hash"])
    if not good:
        raise ApiError(401, "unauthenticated", "wrong email or password")
    with store.tx():
        # The account may have been replaced by a reset or import while hashing.
        current = store.conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        if current is None or current["password_hash"] != user["password_hash"]:
            raise ApiError(401, "unauthenticated", "wrong email or password")
        token = issue_token(user["id"])
    return ok({"user_id": user["id"], "display_name": user["display_name"], "token": token})


# ------------------------------------------------------------------ restaurants

@app.get("/restaurants")
async def list_restaurants():
    rows = store.conn.execute("SELECT id, name, timezone FROM restaurants ORDER BY position").fetchall()
    return ok({"restaurants": [dict(r) for r in rows]})


@app.get("/restaurants/{restaurant_id}")
async def get_restaurant(restaurant_id: str):
    r = store.get_restaurant(restaurant_id)
    if r is None:
        raise not_found("no such restaurant")
    return ok({
        "id": r["id"], "name": r["name"], "timezone": r["timezone"],
        "slot_minutes": r["slot_minutes"],
        "reservation_duration_minutes": r["reservation_duration_minutes"],
        "cancellation_cutoff_minutes": r["cancellation_cutoff_minutes"],
        "opening_hours": r["opening_hours"],
        "tables": [{"id": t["id"], "label": t["label"], "capacity": t["capacity"]}
                   for t in store.get_tables(r["id"])],
    })


@app.get("/availability")
async def availability(request: Request):
    q = request.query_params
    for name in ("restaurant_id", "date", "party_size"):
        if name not in q:
            raise invalid(f"query parameter {name} is required")
    day = parse_date(q["date"])
    raw_party = q["party_size"]
    if not DIGITS_RE.match(raw_party):
        raise invalid("party_size must be written as plain decimal digits")
    party = int(raw_party) if len(raw_party) <= 18 else 10 ** 18
    if party < 1:
        raise invalid("party_size must be at least 1")
    r = store.get_restaurant(q["restaurant_id"])
    if r is None:
        raise not_found("no such restaurant")
    slots = day_slots(r, day)
    tables = [t for t in store.get_tables(r["id"]) if t["capacity"] >= party]
    busy: dict[str, list[tuple[int, int]]] = {}
    if slots and tables:
        lo, hi = min(s[1] for s in slots), max(s[2] for s in slots)
        for row in store.conn.execute(
                "SELECT table_id, start_ts, end_ts FROM reservations WHERE restaurant_id = ? "
                "AND status = 'confirmed' AND start_ts < ? AND end_ts > ?", (r["id"], hi, lo)):
            busy.setdefault(row["table_id"], []).append((row["start_ts"], row["end_ts"]))
    out = []
    for naive, start, end in slots:
        free = [t["id"] for t in tables
                if not any(s < end and e > start for s, e in busy.get(t["id"], ()))]
        out.append({"starts_at_local": format_local(naive), "starts_at": rfc3339(start, r["timezone"]),
                    "available_table_ids": free})
    return ok({"restaurant_id": r["id"], "date": day.isoformat(), "timezone": r["timezone"], "slots": out})


# ------------------------------------------------------------------ reservations

@app.post("/reservations")
async def create_reservation(request: Request):
    user = authenticate(request)
    body = await json_object(request)
    key = idempotency_key(request)
    request_text = canonical(body)
    path = "/reservations"
    with store.tx():
        replay = find_receipt(user["id"], path, key, request_text)
        if replay is not None:
            return replay
        for f in ("restaurant_id", "table_id", "starts_at_local"):
            if f in body and not isinstance(body[f], str):
                raise malformed(f"{f} must be a string")
        for f in ("restaurant_id", "table_id", "starts_at_local", "party_size"):
            if f not in body:
                raise invalid(f"{f} is required")
        party = check_party(body["party_size"])
        naive = parse_local(body["starts_at_local"])
        restaurant = store.get_restaurant(body["restaurant_id"])
        if restaurant is None:
            raise not_found("no such restaurant")
        table = store.get_table(restaurant["id"], body["table_id"])
        if table is None:
            raise not_found("no such table at this restaurant")
        start, end = check_start(restaurant, naive)
        if party > table["capacity"]:
            raise ApiError(422, "party_exceeds_capacity", "party is larger than the table's capacity")
        if store.overlapping(restaurant["id"], table["id"], start, end):
            raise ApiError(409, "table_unavailable", "the table is taken for an overlapping time")
        res_id = new_id("res_", "reservations")
        store.conn.execute(
            "INSERT INTO reservations (id, reference, user_id, restaurant_id, table_id, party_size, "
            "status, starts_at_local, start_ts, end_ts, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, ?, ?)",
            (res_id, new_reference(), user["id"], restaurant["id"], table["id"], party,
             format_local(naive), start, end, now_rfc3339()))
        out = reservation_out(reservation_by_id(res_id))
        store_receipt(user["id"], path, key, request_text, 201, out)
    return ok(out, 201)


@app.get("/reservations")
async def list_reservations(request: Request):
    user = authenticate(request)
    rows = store.conn.execute(RES_SELECT + "WHERE r.user_id = ? ORDER BY r.start_ts DESC, r.rowid DESC",
                              (user["id"],)).fetchall()
    return ok({"reservations": [reservation_out(r) for r in rows]})


@app.get("/reservations/{reference}")
async def get_reservation(reference: str, request: Request):
    user = authenticate(request)
    return ok(reservation_out(own_reservation(user["id"], reference)))


@app.post("/reservations/{reference}/cancel")
async def cancel_reservation(reference: str, request: Request):
    user = authenticate(request)
    with store.tx():
        row = own_reservation(user["id"], reference)
        if row["status"] == "cancelled":
            return ok(reservation_out(row))
        check_cutoff(row, store.get_restaurant(row["restaurant_id"]))
        store.conn.execute("UPDATE reservations SET status = 'cancelled' WHERE id = ?", (row["id"],))
        out = reservation_out(reservation_by_id(row["id"]))
    return ok(out)


@app.patch("/reservations/{reference}")
async def amend_reservation(reference: str, request: Request):
    user = authenticate(request)
    body = await json_object(request)
    with store.tx():
        row = own_reservation(user["id"], reference)
        restaurant = store.get_restaurant(row["restaurant_id"])
        new = plan_amendment(row, restaurant, body, cutoff_first=False)
        if new["moves"] and store.overlapping(restaurant["id"], new["table_id"], new["start_ts"],
                                              new["end_ts"], exclude_ids=(row["id"],)):
            raise ApiError(409, "table_unavailable", "the table is taken for an overlapping time")
        apply_amendment(row, new)
        out = reservation_out(reservation_by_id(row["id"]))
    return ok(out)


# ------------------------------------------------------------------ batch moves (§11)

def validate_moves_shape(body: dict) -> list[dict]:
    moves = body.get("moves", MISSING)
    if moves is MISSING:
        raise invalid("moves is required")
    if not isinstance(moves, list) or not 1 <= len(moves) <= 8:
        raise invalid("moves must be a list of 1 to 8 items")
    refs = set()
    for item in moves:
        if not isinstance(item, dict) or not isinstance(item.get("reference"), str):
            raise invalid("every move needs a string reference")
        if item["reference"] in refs:
            raise invalid("references in one batch must be distinct")
        refs.add(item["reference"])
    return moves


@app.post("/reservation-moves")
async def reservation_moves(request: Request):
    user = authenticate(request)
    body = await json_object(request)
    key = idempotency_key(request)
    request_text = canonical(body)
    path = "/reservation-moves"
    with store.tx():
        replay = find_receipt(user["id"], path, key, request_text)
        if replay is not None:
            return replay
        moves = validate_moves_shape(body)
        rows = [own_reservation(user["id"], m["reference"]) for m in moves]
        if len({r["restaurant_id"] for r in rows}) != 1:
            raise invalid("all bookings in one batch must belong to the same restaurant")
        restaurant = store.get_restaurant(rows[0]["restaurant_id"])
        # Non-occupancy errors first, in input order (cutoff first for each booking).
        plans = [plan_amendment(row, restaurant, m, cutoff_first=True) for row, m in zip(rows, moves)]
        # Occupancy on the final set: listed bookings' old intervals are released,
        # their new ones (changed or not) are held.
        listed = [row["id"] for row in rows]
        for i, plan in enumerate(plans):
            if not plan["moves"]:
                continue
            if store.overlapping(restaurant["id"], plan["table_id"], plan["start_ts"], plan["end_ts"],
                                 exclude_ids=listed):
                raise ApiError(409, "table_unavailable", "a table is taken for an overlapping time")
            for j, other in enumerate(plans):
                if j != i and other["table_id"] == plan["table_id"] \
                        and other["start_ts"] < plan["end_ts"] and other["end_ts"] > plan["start_ts"]:
                    raise ApiError(409, "table_unavailable", "two moved bookings would overlap")
        for row, plan in zip(rows, plans):
            apply_amendment(row, plan)
        out = {"reservations": [reservation_out(reservation_by_id(row["id"])) for row in rows]}
        store_receipt(user["id"], path, key, request_text, 201, out)
    return ok(out, 201)


# ------------------------------------------------------------------ export / import (§10)

@app.get("/_test/export")
async def export_state():
    return ok(transfer.export_state())


@app.post("/_test/import")
async def import_state(request: Request):
    body = await json_object(request)
    rows = transfer.validate_import(body)
    with store.tx():
        transfer.replace_state(rows)
    return Response(status_code=204)
