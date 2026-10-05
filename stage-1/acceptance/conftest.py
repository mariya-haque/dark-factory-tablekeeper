"""Shared helpers for the tablekeeper stage-1 acceptance tests.

Black-box only: every test talks to the running service at BASE_URL over HTTP.
Requirement ids (S1-Rnn) refer to ledger/stage-1/requirements.md.

No test depends on the local time zone database: all expected offsets are written
out literally from the specification's DST table and IANA rules.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8080").rstrip("/")
TIMEOUT = 15.0

# Far-future calendar dates (r_anker is open thu/fri only).
THU = "2030-09-05"          # Thursday, Europe/Berlin summer time (+02:00)
FRI = "2030-09-06"          # Friday
WED = "2030-09-04"          # Wednesday: r_anker closed
THU_WINTER = "2030-12-05"   # Thursday, Europe/Berlin standard time (+01:00)
PAST_THU = "2026-09-24"     # Thursday already in the past (spec example date)

PASSWORDS = {
    "ada": "correct horse",
    "bob": "battery staple",
    "cy": "tr0ub4dor&3",
}
EMAILS = {"ada": "ada@example.com", "bob": "bob@example.com", "cy": "cy@example.com"}

ALL_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

RES_FIELDS = {
    "reservation_id", "reference", "restaurant_id", "table_id", "party_size",
    "status", "starts_at_local", "starts_at", "ends_at", "created_at",
}
REFERENCE_RE = re.compile(r"^[A-Z0-9]{6,12}$")
RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)
LOCAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")


def standard_fixture() -> dict:
    """The fixture every test starts from unless it supplies its own."""
    return copy.deepcopy({
        "users": [
            {"id": "u_ada", "email": EMAILS["ada"], "password": PASSWORDS["ada"], "display_name": "Ada"},
            {"id": "u_bob", "email": EMAILS["bob"], "password": PASSWORDS["bob"], "display_name": "Bob"},
            {"id": "u_cy", "email": EMAILS["cy"], "password": PASSWORDS["cy"], "display_name": "Cy"},
        ],
        "restaurants": [
            {
                "id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin",
                "slot_minutes": 30, "reservation_duration_minutes": 90,
                "cancellation_cutoff_minutes": 120,
                "opening_hours": [
                    {"weekday": "thu", "opens": "18:00", "closes": "23:00"},
                    {"weekday": "fri", "opens": "18:00", "closes": "23:30"},
                ],
                "tables": [
                    {"id": "t_1", "label": "1", "capacity": 2},
                    {"id": "t_2", "label": "2", "capacity": 4},
                    {"id": "t_3", "label": "3", "capacity": 6},
                ],
            },
            {
                "id": "r_other", "name": "Other Place", "timezone": "Europe/Berlin",
                "slot_minutes": 30, "reservation_duration_minutes": 90,
                "cancellation_cutoff_minutes": 120,
                "opening_hours": [{"weekday": "thu", "opens": "18:00", "closes": "23:00"}],
                "tables": [{"id": "o_1", "label": "O1", "capacity": 4}],
            },
            {
                # Non-hour grid: opens 17:15, 45-minute slots, 60-minute stays.
                "id": "r_grid", "name": "Grid House", "timezone": "Europe/Berlin",
                "slot_minutes": 45, "reservation_duration_minutes": 60,
                "cancellation_cutoff_minutes": 60,
                "opening_hours": [{"weekday": "thu", "opens": "17:15", "closes": "21:00"}],
                "tables": [{"id": "g_1", "label": "G1", "capacity": 4}],
            },
            {
                # Tables deliberately not in id order, to check "fixture order".
                "id": "r_order", "name": "Order Hall", "timezone": "Europe/Berlin",
                "slot_minutes": 60, "reservation_duration_minutes": 60,
                "cancellation_cutoff_minutes": 60,
                "opening_hours": [{"weekday": "thu", "opens": "18:00", "closes": "20:00"}],
                "tables": [
                    {"id": "z_9", "label": "Nine", "capacity": 4},
                    {"id": "z_1", "label": "One", "capacity": 2},
                    {"id": "z_5", "label": "Five", "capacity": 8},
                ],
            },
            {
                # Huge cutoff: any booking a few days ahead is already inside it.
                "id": "r_strict", "name": "Strict Bistro", "timezone": "UTC",
                "slot_minutes": 30, "reservation_duration_minutes": 60,
                "cancellation_cutoff_minutes": 20160,
                "opening_hours": [{"weekday": d, "opens": "00:00", "closes": "23:30"} for d in ALL_DAYS],
                "tables": [{"id": "s_1", "label": "S1", "capacity": 4},
                           {"id": "s_2", "label": "S2", "capacity": 4}],
            },
            {
                "id": "r_berlin_night", "name": "Nachteule", "timezone": "Europe/Berlin",
                "slot_minutes": 30, "reservation_duration_minutes": 90,
                "cancellation_cutoff_minutes": 120,
                "opening_hours": [{"weekday": "sun", "opens": "00:00", "closes": "06:00"}],
                "tables": [{"id": "b_1", "label": "B1", "capacity": 4},
                           {"id": "b_2", "label": "B2", "capacity": 4}],
            },
            {
                "id": "r_ny_night", "name": "Night Owl NYC", "timezone": "America/New_York",
                "slot_minutes": 30, "reservation_duration_minutes": 90,
                "cancellation_cutoff_minutes": 120,
                "opening_hours": [{"weekday": "sun", "opens": "00:00", "closes": "06:00"}],
                "tables": [{"id": "n_1", "label": "N1", "capacity": 4},
                           {"id": "n_2", "label": "N2", "capacity": 4}],
            },
            {
                # Many tables for races: k_0 is the contested target.
                "id": "r_busy", "name": "Busy Canteen", "timezone": "Europe/Berlin",
                "slot_minutes": 30, "reservation_duration_minutes": 90,
                "cancellation_cutoff_minutes": 120,
                "opening_hours": [{"weekday": "thu", "opens": "18:00", "closes": "23:00"}],
                "tables": [{"id": f"k_{i}", "label": f"K{i}", "capacity": 4} for i in range(11)],
            },
        ],
        "reservations": [],
    })


# ---------------------------------------------------------------- assertions

def assert_error(r: httpx.Response, status: int, code: str | None = None) -> None:
    assert r.status_code == status, f"expected {status} {code}, got {r.status_code}: {r.text[:500]}"
    try:
        body = r.json()
    except Exception:  # pragma: no cover - reported as failure
        pytest.fail(f"error response is not JSON: {r.text[:300]!r}")
    assert isinstance(body, dict) and isinstance(body.get("error"), dict), f"bad error body: {body!r}"
    assert isinstance(body["error"].get("code"), str), f"error.code missing: {body!r}"
    assert isinstance(body["error"].get("message"), str), f"error.message missing: {body!r}"
    if code is not None:
        assert body["error"]["code"] == code, f"expected code {code}, got {body!r}"


def parse_ts(s: str) -> dt.datetime:
    assert isinstance(s, str) and RFC3339_RE.match(s), f"not RFC 3339 with offset: {s!r}"
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def assert_instant(actual: str, expected: str) -> None:
    """Same instant AND same UTC offset (the offset is part of the requirement)."""
    a, e = parse_ts(actual), parse_ts(expected)
    assert a == e and a.utcoffset() == e.utcoffset(), f"{actual!r} != {expected!r}"


def assert_reservation(res: dict, **expected) -> None:
    assert isinstance(res, dict), res
    missing = RES_FIELDS - set(res)
    assert not missing, f"reservation missing fields {missing}: {res!r}"
    assert REFERENCE_RE.match(res["reference"]), f"bad reference {res['reference']!r}"
    assert isinstance(res["reservation_id"], str) and 0 < len(res["reservation_id"]) <= 64
    assert LOCAL_RE.match(res["starts_at_local"]), res["starts_at_local"]
    parse_ts(res["starts_at"]); parse_ts(res["ends_at"]); parse_ts(res["created_at"])
    assert res["status"] in ("confirmed", "cancelled")
    for k, v in expected.items():
        if k in ("starts_at", "ends_at"):
            assert_instant(res[k], v)
        else:
            assert res[k] == v, f"{k}: expected {v!r}, got {res[k]!r} in {res!r}"


def assert_same_reservation(actual: dict, expected: dict, ignore=()) -> None:
    """Same booking: all ledger fields equal; timestamps equal as instant + offset."""
    assert_reservation(actual)
    for k in RES_FIELDS - set(ignore):
        if k in ("starts_at", "ends_at", "created_at"):
            assert_instant(actual[k], expected[k])
        else:
            assert actual[k] == expected[k], f"{k}: {actual[k]!r} != {expected[k]!r}"


def key() -> str:
    return "k-" + uuid.uuid4().hex


# ---------------------------------------------------------------- API helper

class Api:
    def __init__(self, client: httpx.Client):
        self.c = client
        self._tokens: dict[str, str] = {}

    # control
    def reset(self, fixture: dict | None = None) -> httpx.Response:
        self._tokens.clear()
        r = self.c.post("/_test/reset", json=standard_fixture() if fixture is None else fixture)
        assert r.status_code == 204, f"reset failed: {r.status_code} {r.text[:300]}"
        return r

    # auth
    def login(self, email: str, password: str) -> httpx.Response:
        return self.c.post("/auth/login", json={"email": email, "password": password})

    def token(self, who: str) -> str:
        if who not in self._tokens:
            r = self.login(EMAILS[who], PASSWORDS[who])
            assert r.status_code == 200, f"login {who}: {r.status_code} {r.text[:300]}"
            self._tokens[who] = r.json()["token"]
        return self._tokens[who]

    def auth(self, who_or_token: str, idem: str | None = None) -> dict:
        tok = self.token(who_or_token) if who_or_token in EMAILS else who_or_token
        h = {"Authorization": f"Bearer {tok}"}
        if idem is not None:
            h["Idempotency-Key"] = idem
        return h

    # reservations
    def book(self, who: str, restaurant_id: str, table_id: str, local: str,
             party: int = 2, idem: str | None = None) -> httpx.Response:
        body = {"restaurant_id": restaurant_id, "table_id": table_id,
                "starts_at_local": local, "party_size": party}
        return self.c.post("/reservations", json=body, headers=self.auth(who, idem or key()))

    def booked(self, who: str, restaurant_id: str, table_id: str, local: str, party: int = 2) -> dict:
        r = self.book(who, restaurant_id, table_id, local, party)
        assert r.status_code == 201, f"booking failed: {r.status_code} {r.text[:300]}"
        return r.json()

    def get_res(self, who: str, ref: str) -> httpx.Response:
        return self.c.get(f"/reservations/{ref}", headers=self.auth(who))

    def list_res(self, who: str) -> httpx.Response:
        return self.c.get("/reservations", headers=self.auth(who))

    def cancel(self, who: str, ref: str) -> httpx.Response:
        return self.c.post(f"/reservations/{ref}/cancel", headers=self.auth(who))

    def patch(self, who: str, ref: str, body: dict) -> httpx.Response:
        return self.c.patch(f"/reservations/{ref}", json=body, headers=self.auth(who))

    def moves(self, who: str, moves, idem: str | None = None) -> httpx.Response:
        return self.c.post("/reservation-moves", json={"moves": moves},
                           headers=self.auth(who, idem or key()))

    # public
    def availability(self, restaurant_id: str, date: str, party: int | str = 2, **extra) -> httpx.Response:
        params = {"restaurant_id": restaurant_id, "date": date, "party_size": str(party)}
        params.update(extra)
        return self.c.get("/availability", params=params)

    def slots(self, restaurant_id: str, date: str, party: int = 2) -> dict:
        r = self.availability(restaurant_id, date, party)
        assert r.status_code == 200, f"availability: {r.status_code} {r.text[:300]}"
        return {s["starts_at_local"]: s for s in r.json()["slots"]}

    def free_tables(self, restaurant_id: str, local: str, party: int = 2) -> list:
        date = local[:10]
        return self.slots(restaurant_id, date, party)[local]["available_table_ids"]


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as c:
        r = c.get("/health")
        assert r.status_code == 200, f"service at {BASE_URL} not healthy: {r.status_code}"
        yield c


@pytest.fixture
def api(client) -> Api:
    a = Api(client)
    a.reset()
    return a


# ---------------------------------------------------------------- concurrency

def run_concurrently(n: int, fn):
    """Run fn(i, client) for i in range(n), all released at the same instant.

    Each worker has its own connection so the requests are genuinely in flight
    together. Returns the list of results in index order.
    """
    barrier = threading.Barrier(n)

    def worker(i):
        with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as c:
            barrier.wait()
            return fn(i, c)

    with ThreadPoolExecutor(max_workers=n) as ex:
        return list(ex.map(worker, range(n)))


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))
