"""Cancel and PATCH amendments (spec §8), including the cutoff rule.

Cutoff tests never depend on the time of day: "already started" uses a start in the
past, and "inside the cutoff" uses r_strict (UTC, 14-day cutoff) with a start three
days from today. "Outside the cutoff" uses dates in 2030.
"""
import datetime as dt

import pytest

from conftest import (
    PAST_THU, THU, assert_error, assert_instant, assert_reservation, assert_same_reservation,
)


def soon_local(days=3, hhmm="12:00"):
    d = dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=days)
    return f"{d.isoformat()}T{hhmm}"


# ------------------------------------------------------------------ cancel

def test_cancel_returns_cancelled_and_frees_table(api):
    """S1-R75 S1-R76"""
    res = api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    assert "t_2" not in api.free_tables("r_anker", f"{THU}T19:00")
    r = api.cancel("ada", res["reference"])
    assert r.status_code == 200, r.text
    assert_same_reservation(r.json(), {**res, "status": "cancelled"})
    assert "t_2" in api.free_tables("r_anker", f"{THU}T19:00")
    assert api.get_res("ada", res["reference"]).json()["status"] == "cancelled"
    assert api.book("bob", "r_anker", "t_2", f"{THU}T19:30").status_code == 201


def test_cancel_twice_is_ok(api):
    """S1-R77"""
    res = api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    assert api.cancel("ada", res["reference"]).status_code == 200
    r = api.cancel("ada", res["reference"])
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled" and r.json()["reference"] == res["reference"]


def test_cancel_someone_elses_is_404(api):
    """S1-R79"""
    res = api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    assert_error(api.cancel("bob", res["reference"]), 404, "not_found")
    assert_error(api.cancel("ada", "NOSUCH1"), 404, "not_found")
    assert api.get_res("ada", res["reference"]).json()["status"] == "confirmed"


def test_cancel_after_start_is_cutoff_passed(api):
    """S1-R78 S1-R23"""
    res = api.booked("ada", "r_anker", "t_2", f"{PAST_THU}T19:00")
    assert_error(api.cancel("ada", res["reference"]), 409, "cutoff_passed")
    assert api.get_res("ada", res["reference"]).json()["status"] == "confirmed"


def test_cancel_within_cutoff_is_cutoff_passed(api):
    """S1-R78"""
    res = api.booked("ada", "r_strict", "s_1", soon_local())
    assert_error(api.cancel("ada", res["reference"]), 409, "cutoff_passed")
    assert api.get_res("ada", res["reference"]).json()["status"] == "confirmed"


# ------------------------------------------------------------------ patch

def test_patch_party_size_only(api):
    """S1-R80 S1-R86 (assumption A4: success is 200 with the reservation)"""
    res = api.booked("ada", "r_anker", "t_2", f"{THU}T19:00", party=2)
    r = api.patch("ada", res["reference"], {"party_size": 4})
    assert r.status_code == 200, r.text
    assert_same_reservation(r.json(), {**res, "party_size": 4})


def test_patch_time_moves_occupancy(api):
    """S1-R80 S1-R84 S1-R86"""
    res = api.booked("ada", "r_anker", "t_2", f"{THU}T18:00")
    r = api.patch("ada", res["reference"], {"starts_at_local": f"{THU}T21:00"})
    assert r.status_code == 200, r.text
    new = r.json()
    assert_reservation(new, reservation_id=res["reservation_id"], reference=res["reference"],
                       starts_at_local=f"{THU}T21:00", starts_at=f"{THU}T21:00:00+02:00",
                       ends_at=f"{THU}T22:30:00+02:00", status="confirmed", table_id="t_2")
    assert_instant(new["created_at"], res["created_at"])
    assert "t_2" in api.free_tables("r_anker", f"{THU}T18:00")
    assert "t_2" not in api.free_tables("r_anker", f"{THU}T21:00")
    assert api.get_res("ada", res["reference"]).json()["starts_at_local"] == f"{THU}T21:00"


def test_patch_table_moves_occupancy(api):
    """S1-R84"""
    res = api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    r = api.patch("ada", res["reference"], {"table_id": "t_3"})
    assert r.status_code == 200, r.text
    free = api.free_tables("r_anker", f"{THU}T19:00")
    assert "t_2" in free and "t_3" not in free


def test_patch_overlapping_itself_is_allowed(api):
    """S1-R84: old slot is released together with reserving the new one."""
    res = api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    r = api.patch("ada", res["reference"], {"starts_at_local": f"{THU}T19:30"})
    assert r.status_code == 200, r.text
    assert_error(api.book("bob", "r_anker", "t_2", f"{THU}T20:30"), 409, "table_unavailable")
    assert api.book("bob", "r_anker", "t_2", f"{THU}T18:00").status_code == 201


def test_patch_conflict_leaves_original(api):
    """S1-R67 S1-R85"""
    res = api.booked("ada", "r_anker", "t_2", f"{THU}T18:00")
    api.booked("bob", "r_anker", "t_3", f"{THU}T21:00")
    assert_error(api.patch("ada", res["reference"], {"table_id": "t_3", "starts_at_local": f"{THU}T21:00"}),
                 409, "table_unavailable")
    assert_same_reservation(api.get_res("ada", res["reference"]).json(), res)
    assert_error(api.book("cy", "r_anker", "t_2", f"{THU}T18:30"), 409, "table_unavailable")


@pytest.mark.parametrize("change,status,code", [
    ({"starts_at_local": f"{THU}T19:10"}, 422, "not_on_slot_grid"),
    ({"starts_at_local": f"{THU}T22:00"}, 422, "outside_opening_hours"),
    ({"party_size": 3, "table_id": "t_1"}, 422, "party_exceeds_capacity"),
    ({"party_size": 7}, 422, "party_exceeds_capacity"),
    ({"party_size": 0}, 422, "validation_failed"),
    ({"party_size": "3"}, 422, "validation_failed"),
    ({"starts_at_local": f"{THU}T19:00:00"}, 422, "validation_failed"),
    ({"table_id": "o_1"}, 404, "not_found"),
    ({"table_id": "t_nope"}, 404, "not_found"),
    ({"table_id": 5}, 400, "malformed_request"),
])
def test_patch_validation_like_create(api, change, status, code):
    """S1-R81 S1-R85"""
    res = api.booked("ada", "r_anker", "t_2", f"{THU}T19:00", party=2)
    assert_error(api.patch("ada", res["reference"], change), status, code)
    assert_same_reservation(api.get_res("ada", res["reference"]).json(), res)
    assert "t_2" not in api.free_tables("r_anker", f"{THU}T19:00")


def test_patch_cancelled_is_409(api):
    """S1-R83"""
    res = api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    api.cancel("ada", res["reference"])
    assert_error(api.patch("ada", res["reference"], {"party_size": 3}), 409, "reservation_cancelled")


def test_patch_cutoff_uses_current_start(api):
    """S1-R82: a past booking cannot be moved into the future."""
    res = api.booked("ada", "r_anker", "t_2", f"{PAST_THU}T19:00")
    assert_error(api.patch("ada", res["reference"], {"starts_at_local": f"{THU}T19:00"}), 409, "cutoff_passed")
    assert "t_2" in api.free_tables("r_anker", f"{THU}T19:00")


def test_patch_within_cutoff(api):
    """S1-R82"""
    res = api.booked("ada", "r_strict", "s_1", soon_local())
    assert_error(api.patch("ada", res["reference"], {"table_id": "s_2"}), 409, "cutoff_passed")


def test_patch_someone_elses_is_404(api):
    """S1-R87"""
    res = api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    assert_error(api.patch("bob", res["reference"], {"party_size": 3}), 404, "not_found")
    assert api.get_res("ada", res["reference"]).json()["party_size"] == 2
