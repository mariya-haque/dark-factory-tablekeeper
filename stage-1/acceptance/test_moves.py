"""Atomic reservation moves (spec §11)."""
import pytest

from conftest import (
    PAST_THU, THU, assert_error, assert_reservation, assert_same_reservation, key,
)

T19 = f"{THU}T19:00"


@pytest.fixture
def two(api):
    """ada: A on t_1 and B on t_2, both 19:00; bob: C on t_3 at 19:00."""
    a = api.booked("ada", "r_anker", "t_1", T19)
    b = api.booked("ada", "r_anker", "t_2", T19)
    c = api.booked("bob", "r_anker", "t_3", T19)
    return a, b, c


def unchanged(api, *res):
    for r in res:
        assert_same_reservation(api.get_res(_owner(api, r), r["reference"]).json(), r)


def _owner(api, r):
    return "ada" if api.get_res("ada", r["reference"]).status_code == 200 else "bob"


def test_swap_tables_succeeds(api, two):
    """S1-R111 S1-R114 S1-R106 S1-R107"""
    a, b, _ = two
    r = api.moves("ada", [{"reference": a["reference"], "table_id": "t_2"},
                          {"reference": b["reference"], "table_id": "t_1"}])
    assert r.status_code == 201, r.text
    out = r.json()["reservations"]
    assert [x["reference"] for x in out] == [a["reference"], b["reference"]]
    assert_same_reservation(out[0], {**a, "table_id": "t_2"})
    assert_same_reservation(out[1], {**b, "table_id": "t_1"})
    assert api.get_res("ada", a["reference"]).json()["table_id"] == "t_2"
    assert api.get_res("ada", b["reference"]).json()["table_id"] == "t_1"


def test_result_includes_unchanged_items_in_input_order(api, two):
    """S1-R114 S1-R116"""
    a, b, _ = two
    r = api.moves("ada", [{"reference": b["reference"]},
                          {"reference": a["reference"], "starts_at_local": f"{THU}T21:00"}])
    assert r.status_code == 201, r.text
    out = r.json()["reservations"]
    assert [x["reference"] for x in out] == [b["reference"], a["reference"]]
    assert_same_reservation(out[0], b)
    assert_reservation(out[1], starts_at_local=f"{THU}T21:00", starts_at=f"{THU}T21:00:00+02:00",
                       ends_at=f"{THU}T22:30:00+02:00", table_id="t_1",
                       reservation_id=a["reservation_id"], reference=a["reference"])
    assert_same_reservation(api.get_res("ada", b["reference"]).json(), b)
    assert "t_1" in api.free_tables("r_anker", T19)


def test_omitted_fields_kept_and_unknown_ignored(api, two):
    """S1-R106"""
    a, _, _ = two
    r = api.moves("ada", [{"reference": a["reference"], "party_size": 1, "colour": "red"}])
    assert r.status_code == 201, r.text
    assert_same_reservation(r.json()["reservations"][0], {**a, "party_size": 1})


def test_conflict_with_unlisted_booking_changes_nothing(api, two):
    """S1-R111 S1-R113 S1-R85"""
    a, b, c = two
    r = api.moves("ada", [{"reference": b["reference"], "table_id": "t_1",
                           "starts_at_local": f"{THU}T21:00"},
                          {"reference": a["reference"], "table_id": "t_3"}])  # t_3 held by bob
    assert_error(r, 409, "table_unavailable")
    assert_same_reservation(api.get_res("ada", a["reference"]).json(), a)
    assert_same_reservation(api.get_res("ada", b["reference"]).json(), b)
    assert "t_1" in api.free_tables("r_anker", f"{THU}T21:00")
    assert "t_2" not in api.free_tables("r_anker", T19)


def test_conflict_among_results(api, two):
    """S1-R111 S1-R113"""
    a, b, _ = two
    r = api.moves("ada", [{"reference": a["reference"], "starts_at_local": f"{THU}T21:00"},
                          {"reference": b["reference"], "table_id": "t_1", "starts_at_local": f"{THU}T21:30"}])
    assert_error(r, 409, "table_unavailable")
    unchanged(api, a, b)


def test_unchanged_listed_booking_keeps_occupancy(api, two):
    """S1-R112"""
    a, b, _ = two
    r = api.moves("ada", [{"reference": a["reference"]},
                          {"reference": b["reference"], "table_id": "t_1"}])
    assert_error(r, 409, "table_unavailable")
    unchanged(api, a, b)


@pytest.mark.parametrize("moves", [
    [],
    [{"table_id": "t_1"}],                                   # item without reference
    [{"reference": 12345}],                                  # reference not a string
    [{"reference": "AAAAAA"}] * 9,                           # 9 items (and duplicates)
])
def test_invalid_shape(api, moves):
    """S1-R103"""
    assert_error(api.moves("ada", moves), 422, "validation_failed")


def test_nine_distinct_items_rejected_eight_accepted(api):
    """S1-R103 (boundary)"""
    refs = [api.booked("ada", "r_busy", f"k_{i}", T19)["reference"] for i in range(1, 10)]
    assert_error(api.moves("ada", [{"reference": r} for r in refs]), 422, "validation_failed")
    r = api.moves("ada", [{"reference": r} for r in refs[:8]])
    assert r.status_code == 201, r.text
    assert len(r.json()["reservations"]) == 8


def test_duplicate_references(api, two):
    """S1-R103"""
    a, _, _ = two
    r = api.moves("ada", [{"reference": a["reference"], "table_id": "t_2"},
                          {"reference": a["reference"], "table_id": "t_3"}])
    assert_error(r, 422, "validation_failed")


def test_missing_moves_field(api):
    """S1-R103 S1-R28"""
    r = api.c.post("/reservation-moves", json={"items": []}, headers=api.auth("ada", key()))
    assert_error(r, 422, "validation_failed")


def test_unknown_or_foreign_reference_404(api, two):
    """S1-R104"""
    a, _, c = two
    assert_error(api.moves("ada", [{"reference": a["reference"], "table_id": "t_2"},
                                   {"reference": c["reference"], "table_id": "t_1"}]), 404, "not_found")
    assert_error(api.moves("ada", [{"reference": "NOSUCH9", "table_id": "t_1"}]), 404, "not_found")
    unchanged(api, a, c)


def test_different_restaurants_422(api, two):
    """S1-R105"""
    a, _, _ = two
    o = api.booked("ada", "r_other", "o_1", T19)
    assert_error(api.moves("ada", [{"reference": a["reference"]}, {"reference": o["reference"]}]),
                 422, "validation_failed")


def test_cancelled_booking_409(api, two):
    """S1-R108"""
    a, b, _ = two
    api.cancel("ada", b["reference"])
    assert_error(api.moves("ada", [{"reference": a["reference"], "table_id": "t_3"},
                                   {"reference": b["reference"], "table_id": "t_1"}]),
                 409, "reservation_cancelled")


def test_cutoff_applies(api):
    """S1-R109"""
    p = api.booked("ada", "r_anker", "t_1", f"{PAST_THU}T19:00")
    assert_error(api.moves("ada", [{"reference": p["reference"], "table_id": "t_2"}]), 409, "cutoff_passed")
    assert_same_reservation(api.get_res("ada", p["reference"]).json(), p)


def test_non_occupancy_error_beats_earlier_occupancy_error(api, two):
    """S1-R110"""
    a, b, _ = two
    r = api.moves("ada", [{"reference": a["reference"], "table_id": "t_3"},      # occupied -> 409
                          {"reference": b["reference"], "party_size": 99}])       # exceeds -> 422
    assert_error(r, 422, "party_exceeds_capacity")


def test_non_occupancy_errors_in_input_order(api, two):
    """S1-R110"""
    a, b, _ = two
    api.cancel("ada", b["reference"])
    assert_error(api.moves("ada", [{"reference": a["reference"], "party_size": 99},
                                   {"reference": b["reference"], "table_id": "t_3"}]),
                 422, "party_exceeds_capacity")
    assert_error(api.moves("ada", [{"reference": b["reference"], "table_id": "t_3"},
                                   {"reference": a["reference"], "party_size": 99}]),
                 409, "reservation_cancelled")
    assert_error(api.moves("ada", [{"reference": a["reference"], "starts_at_local": f"{THU}T19:10"},
                                   {"reference": b["reference"]}]),
                 422, "not_on_slot_grid")


def test_cutoff_precedes_other_errors_for_same_booking(api):
    """S1-R110 S1-R109"""
    p = api.booked("ada", "r_anker", "t_1", f"{PAST_THU}T19:00")
    q = api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    assert_error(api.moves("ada", [{"reference": p["reference"], "party_size": 99}]), 409, "cutoff_passed")
    assert_error(api.moves("ada", [{"reference": q["reference"], "party_size": 99},
                                   {"reference": p["reference"], "table_id": "t_3"}]),
                 422, "party_exceeds_capacity")


def test_failed_batch_does_not_consume_key(api, two):
    """S1-R113 S1-R52"""
    a, b, _ = two
    k = key()
    assert_error(api.moves("ada", [{"reference": a["reference"], "table_id": "t_3"}], idem=k),
                 409, "table_unavailable")
    r = api.moves("ada", [{"reference": a["reference"], "starts_at_local": f"{THU}T21:00"}], idem=k)
    assert r.status_code == 201, r.text


def test_replay_returns_original_after_changes(api, two):
    """S1-R115 S1-R50 S1-R55"""
    a, b, _ = two
    k = key()
    moves = [{"reference": a["reference"], "table_id": "t_2"}, {"reference": b["reference"], "table_id": "t_1"}]
    first = api.moves("ada", moves, idem=k)
    assert first.status_code == 201, first.text
    assert api.cancel("ada", a["reference"]).status_code == 200
    assert api.patch("ada", b["reference"], {"party_size": 1}).status_code == 200
    again = api.moves("ada", moves, idem=k)
    assert again.status_code == 200, again.text
    assert again.json() == first.json()
    assert api.get_res("ada", a["reference"]).json()["status"] == "cancelled"
    assert api.get_res("ada", b["reference"]).json()["party_size"] == 1
    assert_error(api.moves("ada", moves[:1], idem=k), 409, "idempotency_key_reuse")


def test_no_token_401(api, two):
    """S1-R102"""
    a, _, _ = two
    r = api.c.post("/reservation-moves", json={"moves": [{"reference": a["reference"], "table_id": "t_3"}]},
                   headers={"Idempotency-Key": key()})
    assert_error(r, 401, "unauthenticated")


def test_identity_owner_creation_time_never_change(api, two):
    """S1-R107"""
    a, _, _ = two
    r = api.moves("ada", [{"reference": a["reference"], "starts_at_local": f"{THU}T21:00",
                           "reservation_id": "hacked", "created_at": "2000-01-01T00:00:00+00:00",
                           "user_id": "u_bob", "reference_new": "X"}])
    assert r.status_code == 201, r.text
    out = r.json()["reservations"][0]
    assert_same_reservation(out, {**a, "starts_at_local": f"{THU}T21:00",
                                  "starts_at": f"{THU}T21:00:00+02:00", "ends_at": f"{THU}T22:30:00+02:00"})
    assert_error(api.get_res("bob", a["reference"]), 404, "not_found")
