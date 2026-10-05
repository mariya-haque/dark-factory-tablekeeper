"""Time zones and daylight-saving transitions (spec §9).

r_berlin_night and r_ny_night are open Sundays 00:00-06:00, 30-minute slots, 90-minute
stays. Expected offsets are written out from the specification's transition table.
"""
import pytest

from conftest import assert_error, assert_reservation

BER_SPRING = "2026-03-29"   # 02:00 -> 03:00, +01:00 -> +02:00
BER_FALL = "2026-10-25"     # 03:00 -> 02:00, +02:00 -> +01:00
NY_SPRING = "2026-03-08"    # 02:00 -> 03:00, -05:00 -> -04:00
NY_FALL = "2026-11-01"      # 02:00 -> 01:00, -04:00 -> -05:00

ALL = ["00:00", "00:30", "01:00", "01:30", "02:00", "02:30", "03:00", "03:30", "04:00", "04:30"]


def offsets(api, rid, date):
    r = api.availability(rid, date, 2)
    assert r.status_code == 200, r.text
    return [(s["starts_at_local"][11:], s["starts_at"][-6:]) for s in r.json()["slots"]]


@pytest.mark.parametrize("rid,date,before,after", [
    ("r_berlin_night", BER_SPRING, "+01:00", "+02:00"),
    ("r_ny_night", NY_SPRING, "-05:00", "-04:00"),
])
def test_spring_forward_gap_absent_from_availability(api, rid, date, before, after):
    """S1-R88 S1-R92 S1-R60"""
    got = offsets(api, rid, date)
    expected = [(t, before) for t in ALL[:4]] + [(t, after) for t in ALL[6:]]
    assert got == expected


@pytest.mark.parametrize("rid,date,first,second,repeated", [
    ("r_berlin_night", BER_FALL, "+02:00", "+01:00", {"02:00", "02:30"}),
    ("r_ny_night", NY_FALL, "-04:00", "-05:00", {"01:00", "01:30"}),
])
def test_fall_back_repeated_hour_listed_once_first_occurrence(api, rid, date, first, second, repeated):
    """S1-R90 S1-R92 S1-R60"""
    got = offsets(api, rid, date)
    times = [t for t, _ in got]
    assert times == ALL, times  # every wall-clock slot exactly once
    last_repeated = max(repeated)
    for t, off in got:
        assert off == (first if t <= last_repeated else second), (t, off)


@pytest.mark.parametrize("rid,table,local", [
    ("r_berlin_night", "b_1", f"{BER_SPRING}T02:00"),
    ("r_berlin_night", "b_1", f"{BER_SPRING}T02:30"),
    ("r_ny_night", "n_1", f"{NY_SPRING}T02:30"),
])
def test_booking_in_gap_is_invalid_local_time(api, rid, table, local):
    """S1-R71 S1-R89"""
    assert_error(api.book("ada", rid, table, local), 422, "invalid_local_time")


def test_patch_into_gap_is_invalid_local_time(api):
    """S1-R89 S1-R81"""
    res = api.booked("ada", "r_berlin_night", "b_1", f"{BER_SPRING}T01:00")
    assert_error(api.patch("ada", res["reference"], {"starts_at_local": f"{BER_SPRING}T02:30"}),
                 422, "invalid_local_time")


def test_spring_forward_absolute_duration_berlin(api):
    """S1-R91: 01:30 CET + 90 real minutes = 04:00 CEST."""
    res = api.booked("ada", "r_berlin_night", "b_1", f"{BER_SPRING}T01:30")
    assert_reservation(res, starts_at=f"{BER_SPRING}T01:30:00+01:00", ends_at=f"{BER_SPRING}T04:00:00+02:00")
    assert_error(api.book("bob", "r_berlin_night", "b_1", f"{BER_SPRING}T03:30"), 409, "table_unavailable")
    assert api.book("bob", "r_berlin_night", "b_1", f"{BER_SPRING}T04:00").status_code == 201
    slots = {s["starts_at_local"][11:]: s["available_table_ids"]
             for s in api.availability("r_berlin_night", BER_SPRING, 2).json()["slots"]}
    # ada [00:30Z, 02:00Z), bob [02:00Z, 03:30Z): only 00:00 (23:00Z-00:30Z) is free for b_1
    assert "b_1" in slots["00:00"]
    assert all("b_1" not in slots[t] for t in ("00:30", "01:00", "01:30", "03:00", "03:30", "04:00", "04:30"))
    assert all("b_2" in ids for ids in slots.values())


def test_spring_forward_absolute_duration_ny(api):
    """S1-R91"""
    res = api.booked("ada", "r_ny_night", "n_1", f"{NY_SPRING}T01:30")
    assert_reservation(res, starts_at=f"{NY_SPRING}T01:30:00-05:00", ends_at=f"{NY_SPRING}T04:00:00-04:00")


def test_fall_back_ny_spec_example(api):
    """S1-R90 S1-R91: 01:30 first occurrence + 90 minutes reads 02:00 local."""
    res = api.booked("ada", "r_ny_night", "n_1", f"{NY_FALL}T01:30")
    assert_reservation(res, starts_at_local=f"{NY_FALL}T01:30",
                       starts_at=f"{NY_FALL}T01:30:00-04:00", ends_at=f"{NY_FALL}T02:00:00-05:00")
    # [05:30Z, 07:00Z) does not overlap 02:00 EST (07:00Z)
    assert api.book("bob", "r_ny_night", "n_1", f"{NY_FALL}T02:00").status_code == 201


def test_fall_back_berlin_overlap_on_real_instants(api):
    """S1-R90 S1-R91 S1-R02"""
    res = api.booked("ada", "r_berlin_night", "b_1", f"{BER_FALL}T02:00")
    # 02:00 CEST = 00:00Z; ends 01:30Z = 02:30 CET
    assert_reservation(res, starts_at=f"{BER_FALL}T02:00:00+02:00", ends_at=f"{BER_FALL}T02:30:00+01:00")
    assert_error(api.book("bob", "r_berlin_night", "b_1", f"{BER_FALL}T02:30"), 409, "table_unavailable")
    # 03:00 CET = 02:00Z, after the end
    r = api.book("bob", "r_berlin_night", "b_1", f"{BER_FALL}T03:00")
    assert r.status_code == 201, r.text
    assert_reservation(r.json(), starts_at=f"{BER_FALL}T03:00:00+01:00", ends_at=f"{BER_FALL}T04:30:00+01:00")


def test_fall_back_berlin_end_in_second_occurrence(api):
    """S1-R91: 01:30 CEST (23:30Z) + 90 min = 01:00Z = 02:00 CET."""
    res = api.booked("ada", "r_berlin_night", "b_2", f"{BER_FALL}T01:30")
    assert_reservation(res, starts_at=f"{BER_FALL}T01:30:00+02:00", ends_at=f"{BER_FALL}T02:00:00+01:00")
    assert_error(api.book("bob", "r_berlin_night", "b_2", f"{BER_FALL}T02:30"), 409, "table_unavailable")
    slots = {s["starts_at_local"][11:]: s["available_table_ids"]
             for s in api.availability("r_berlin_night", BER_FALL, 2).json()["slots"]}
    assert "b_2" not in slots["02:30"]
    assert "b_2" in slots["03:00"]
