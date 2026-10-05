"""POST/GET /reservations (spec §1, §5, §8)."""
import pytest

from conftest import (
    THU, THU_WINTER, PAST_THU, assert_error, assert_reservation, assert_same_reservation, key,
    parse_ts,
)


def post(api, body, who="ada"):
    return api.c.post("/reservations", json=body, headers=api.auth(who, key()))


def body(**over):
    b = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": f"{THU}T19:00", "party_size": 4}
    b.update(over)
    return b


def test_create_reservation_shape(api):
    """S1-R64 S1-R65 S1-R66 S1-R14 S1-R70 (party == capacity is allowed)"""
    r = post(api, body())
    assert r.status_code == 201, r.text
    res = r.json()
    assert_reservation(res, restaurant_id="r_anker", table_id="t_2", party_size=4, status="confirmed",
                       starts_at_local=f"{THU}T19:00", starts_at=f"{THU}T19:00:00+02:00",
                       ends_at=f"{THU}T20:30:00+02:00")


def test_winter_booking_offset(api):
    """S1-R65 S1-R92"""
    res = api.booked("ada", "r_anker", "t_2", f"{THU_WINTER}T19:00")
    assert_reservation(res, starts_at=f"{THU_WINTER}T19:00:00+01:00", ends_at=f"{THU_WINTER}T20:30:00+01:00")


def test_references_unique_and_well_formed(api):
    """S1-R66"""
    refs, ids = set(), set()
    for t in ("t_1", "t_2", "t_3"):
        for hh in ("18:00", "19:30", "21:00"):
            res = api.booked("ada", "r_anker", t, f"{THU}T{hh}")
            refs.add(res["reference"]); ids.add(res["reservation_id"])
    assert len(refs) == 9 and len(ids) == 9


def test_overlap_rejected(api):
    """S1-R01 S1-R67"""
    api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    for hh in ("18:00", "18:30", "19:00", "19:30", "20:00"):
        assert_error(api.book("bob", "r_anker", "t_2", f"{THU}T{hh}"), 409, "table_unavailable")
    # other tables unaffected
    assert api.book("bob", "r_anker", "t_3", f"{THU}T19:00").status_code == 201


def test_half_open_back_to_back_allowed(api):
    """S1-R02: [19:00, 20:30) does not overlap a booking starting at 20:30, in either order."""
    api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    assert api.book("bob", "r_anker", "t_2", f"{THU}T20:30").status_code == 201
    api.booked("ada", "r_anker", "t_1", f"{THU}T20:30")
    assert api.book("bob", "r_anker", "t_1", f"{THU}T19:00").status_code == 201


def test_cancelled_booking_does_not_block(api):
    """S1-R76 S1-R67"""
    res = api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    assert api.cancel("ada", res["reference"]).status_code == 200
    assert api.book("bob", "r_anker", "t_2", f"{THU}T19:00").status_code == 201


@pytest.mark.parametrize("local", [f"{THU}T19:15", f"{THU}T19:01", f"{THU}T18:50"])
def test_not_on_slot_grid(api, local):
    """S1-R68"""
    assert_error(post(api, body(starts_at_local=local)), 422, "not_on_slot_grid")


def test_grid_anchored_at_opening(api):
    """S1-R20 S1-R68"""
    assert post(api, body(restaurant_id="r_grid", table_id="g_1", starts_at_local=f"{THU}T18:45")).status_code == 201
    assert_error(post(api, body(restaurant_id="r_grid", table_id="g_1", starts_at_local=f"{THU}T18:30")),
                 422, "not_on_slot_grid")


@pytest.mark.parametrize("local", [
    f"{THU}T22:00",   # ends 23:30 > closes 23:00
    f"{THU}T22:30",   # ends at midnight, after closes
    f"{THU}T23:00",   # starts at closing time
    "2030-09-04T19:00",  # Wednesday: closed
])
def test_outside_opening_hours(api, local):
    """S1-R69 S1-R19"""
    assert_error(post(api, body(starts_at_local=local)), 422, "outside_opening_hours")


def test_last_slot_ending_exactly_at_close_allowed(api):
    """S1-R60 S1-R69 (boundary)"""
    assert post(api, body(starts_at_local=f"{THU}T21:30")).status_code == 201


def test_party_exceeds_capacity(api):
    """S1-R70"""
    assert_error(post(api, body(table_id="t_1", party_size=3)), 422, "party_exceeds_capacity")
    assert post(api, body(table_id="t_1", party_size=2)).status_code == 201


@pytest.mark.parametrize("party", [0, -1, 2.5, "4", True, False, None, "four"])
def test_invalid_party_size(api, party):
    """S1-R30 S1-R64"""
    assert_error(post(api, body(party_size=party)), 422, "validation_failed")


@pytest.mark.parametrize("local", [
    f"{THU}T19:00:00", f"{THU}T19:00+02:00", f"{THU}T19:00Z", f"{THU} 19:00", "19:00",
    f"{THU}", "2030-9-5T19:00", f"{THU}T7:00", "", "2030-02-30T19:00", f"{THU}T25:00",
])
def test_invalid_starts_at_local(api, local):
    """S1-R31 S1-R29"""
    assert_error(post(api, body(starts_at_local=local)), 422, "validation_failed")


@pytest.mark.parametrize("missing", ["restaurant_id", "table_id", "starts_at_local", "party_size"])
def test_missing_field(api, missing):
    """S1-R28"""
    b = body(); del b[missing]
    assert_error(post(api, b), 422, "validation_failed")


@pytest.mark.parametrize("field,value", [
    ("restaurant_id", 123), ("table_id", ["t_2"]), ("starts_at_local", 1900), ("table_id", {"id": "t_2"}),
])
def test_wrong_json_type_is_malformed(api, field, value):
    """S1-R25 S1-R31"""
    assert_error(post(api, body(**{field: value})), 400, "malformed_request")


@pytest.mark.parametrize("raw", [b"{", b"not json", b"[1, 2]", b'"string"'])
def test_unparseable_or_non_object_body(api, raw):
    """S1-R25"""
    r = api.c.post("/reservations", content=raw,
                   headers={**api.auth("ada", key()), "Content-Type": "application/json"})
    assert_error(r, 400, "malformed_request")


@pytest.mark.parametrize("over", [
    {"restaurant_id": "r_nope"},
    {"table_id": "t_nope"},
    {"table_id": "o_1"},  # belongs to r_other
])
def test_unknown_restaurant_or_table(api, over):
    """S1-R72"""
    assert_error(post(api, body(**over)), 404, "not_found")


def test_list_own_reservations_desc(api):
    """S1-R73"""
    assert api.list_res("ada").json() == {"reservations": []}
    a = api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    b = api.booked("ada", "r_anker", "t_2", f"{THU_WINTER}T18:00")
    c = api.booked("ada", "r_anker", "t_1", f"{PAST_THU}T20:00")
    d = api.booked("ada", "r_anker", "t_1", f"{THU}T21:00")
    api.booked("bob", "r_anker", "t_3", f"{THU}T19:00")
    assert api.cancel("ada", a["reference"]).status_code == 200
    r = api.list_res("ada")
    assert r.status_code == 200
    got = r.json()["reservations"]
    assert [x["reference"] for x in got] == [b["reference"], d["reference"], a["reference"], c["reference"]]
    starts = [parse_ts(x["starts_at"]) for x in got]
    assert starts == sorted(starts, reverse=True)
    by_ref = {x["reference"]: x for x in got}
    assert by_ref[a["reference"]]["status"] == "cancelled"
    assert by_ref[b["reference"]]["status"] == "confirmed"
    for x in got:
        assert_reservation(x)


def test_get_reservation_owner_only(api):
    """S1-R74"""
    res = api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    r = api.get_res("ada", res["reference"])
    assert r.status_code == 200
    assert_same_reservation(r.json(), res)
    assert_error(api.get_res("bob", res["reference"]), 404, "not_found")
    assert_error(api.get_res("ada", "ZZZZZZ9"), 404, "not_found")


def test_rejected_requests_create_nothing(api):
    """S1-R03"""
    api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    post(api, body(starts_at_local=f"{THU}T19:30"), who="bob")       # 409
    post(api, body(table_id="t_1", party_size=5), who="bob")          # 422
    post(api, body(starts_at_local=f"{THU}T19:10"), who="bob")       # 422
    assert api.list_res("bob").json() == {"reservations": []}
    assert "t_1" in api.free_tables("r_anker", f"{THU}T19:00")
