"""Restaurants and availability (spec §4, §8 public endpoints)."""
import pytest

from conftest import THU, THU_WINTER, FRI, WED, assert_error, assert_instant, standard_fixture


def test_list_restaurants(api, client):
    """S1-R56"""
    r = client.get("/restaurants")
    assert r.status_code == 200
    body = r.json()
    got = {x["id"]: x for x in body["restaurants"]}
    for fx in standard_fixture()["restaurants"]:
        assert fx["id"] in got
        assert got[fx["id"]]["name"] == fx["name"]
        assert got[fx["id"]]["timezone"] == fx["timezone"]
    assert len(got) == len(standard_fixture()["restaurants"])


def test_get_restaurant_in_fixture_shape(api, client):
    """S1-R57 S1-R18"""
    fx = standard_fixture()["restaurants"][0]
    r = client.get("/restaurants/r_anker")
    assert r.status_code == 200, r.text
    body = r.json()
    for f in ("id", "name", "timezone", "slot_minutes", "reservation_duration_minutes",
              "cancellation_cutoff_minutes"):
        assert body[f] == fx[f], f
    norm = lambda hs: sorted((h["weekday"], h["opens"], h["closes"]) for h in hs)
    assert norm(body["opening_hours"]) == norm(fx["opening_hours"])
    assert [(t["id"], t["label"], t["capacity"]) for t in body["tables"]] == \
           [(t["id"], t["label"], t["capacity"]) for t in fx["tables"]]


def test_get_unknown_restaurant_404(api, client):
    """S1-R57 S1-R27"""
    assert_error(client.get("/restaurants/r_nope"), 404, "not_found")


def test_availability_shape_and_grid_thursday(api):
    """S1-R58 S1-R59 S1-R60 S1-R61 S1-R65 S1-R92"""
    r = api.availability("r_anker", THU, 2)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["restaurant_id"] == "r_anker"
    assert body["date"] == THU
    assert body["timezone"] == "Europe/Berlin"
    times = [s["starts_at_local"] for s in body["slots"]]
    # 18:00..21:30: last slot 21:30 + 90 min == 23:00 == closes.
    assert times == [f"{THU}T{h}" for h in
                     ["18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00", "21:30"]]
    for s in body["slots"]:
        assert_instant(s["starts_at"], s["starts_at_local"] + ":00+02:00")
        assert s["available_table_ids"] == ["t_1", "t_2", "t_3"]


def test_availability_grid_friday_later_close(api):
    """S1-R60 S1-R19"""
    times = list(api.slots("r_anker", FRI, 2))
    assert times[0] == f"{FRI}T18:00" and times[-1] == f"{FRI}T22:00" and len(times) == 9


def test_availability_grid_from_opening_time(api):
    """S1-R20 S1-R60: 45-minute grid anchored at 17:15."""
    times = list(api.slots("r_grid", THU, 2))
    assert times == [f"{THU}T17:15", f"{THU}T18:00", f"{THU}T18:45", f"{THU}T19:30"]


def test_winter_offset(api):
    """S1-R92 S1-R65"""
    s = api.slots("r_anker", THU_WINTER, 2)[f"{THU_WINTER}T19:00"]
    assert_instant(s["starts_at"], f"{THU_WINTER}T19:00:00+01:00")


def test_closed_day_has_no_slots(api):
    """S1-R62 S1-R19"""
    r = api.availability("r_anker", WED, 2)
    assert r.status_code == 200, r.text
    assert r.json()["slots"] == []


def test_capacity_filter_and_empty_slots_still_listed(api):
    """S1-R61"""
    assert api.free_tables("r_anker", f"{THU}T19:00", 2) == ["t_1", "t_2", "t_3"]
    assert api.free_tables("r_anker", f"{THU}T19:00", 3) == ["t_2", "t_3"]
    assert api.free_tables("r_anker", f"{THU}T19:00", 4) == ["t_2", "t_3"]
    assert api.free_tables("r_anker", f"{THU}T19:00", 6) == ["t_3"]
    slots = api.slots("r_anker", THU, 7)
    assert len(slots) == 8
    assert all(s["available_table_ids"] == [] for s in slots.values())


def test_fixture_order_of_tables(api):
    """S1-R61"""
    assert api.free_tables("r_order", f"{THU}T18:00", 1) == ["z_9", "z_1", "z_5"]
    assert api.free_tables("r_order", f"{THU}T18:00", 3) == ["z_9", "z_5"]


def test_booking_removes_table_from_overlapping_slots_only(api):
    """S1-R61 S1-R02: 19:00 booking occupies [19:00, 20:30)."""
    api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    slots = api.slots("r_anker", THU, 2)
    for t, free in [("18:00", True), ("18:30", False), ("19:00", False), ("19:30", False),
                    ("20:00", False), ("20:30", True), ("21:00", True)]:
        assert ("t_2" in slots[f"{THU}T{t}"]["available_table_ids"]) == free, t
        assert "t_1" in slots[f"{THU}T{t}"]["available_table_ids"]


def test_starts_at_local_round_trips_into_booking(api):
    """S1-R59"""
    slot = api.slots("r_anker", THU, 2)[f"{THU}T21:30"]
    r = api.book("ada", "r_anker", slot["available_table_ids"][0], slot["starts_at_local"])
    assert r.status_code == 201, r.text


@pytest.mark.parametrize("missing", ["restaurant_id", "date", "party_size"])
def test_availability_missing_param(api, client, missing):
    """S1-R58 S1-R28"""
    params = {"restaurant_id": "r_anker", "date": THU, "party_size": "2"}
    del params[missing]
    assert_error(client.get("/availability", params=params), 422, "validation_failed")


@pytest.mark.parametrize("party", ["1e9", "4.0", "+4", "abc", "0", "-1", "", " 4"])
def test_availability_bad_party_size(api, party):
    """S1-R32 S1-R29 S1-R30"""
    assert_error(api.availability("r_anker", THU, party), 422, "validation_failed")


@pytest.mark.parametrize("date", ["2030-02-30", "2030-13-01", "20300905", "05.09.2030",
                                  "2030-9-5", "", "2030-09-05T19:00"])
def test_availability_bad_date(api, date):
    """S1-R29"""
    assert_error(api.availability("r_anker", date, 2), 422, "validation_failed")


def test_availability_unknown_restaurant(api):
    """S1-R63 (assumption A3: unknown restaurant is 404 not_found)"""
    assert_error(api.availability("r_nope", THU, 2), 404, "not_found")
