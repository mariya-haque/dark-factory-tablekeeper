"""Concurrency and load (spec §1, §2, §5, §7, §8, §11).

r_busy has tables k_0..k_10 (capacity 4), open Thursdays 18:00-23:00. k_0 is the
contested table in every race. Every race asserts exactly one winner, that the losers
get the specified error (never 5xx), and that the final state holds one booking.
"""
import time

from conftest import THU, assert_error, key, run_concurrently, standard_fixture

T19 = f"{THU}T19:00"


def seeded(n):
    """Fixture with n confirmed seeded bookings for ada on k_1..k_n at 19:00."""
    fx = standard_fixture()
    fx["reservations"] = [{
        "id": f"res_seed_{i}", "reference": f"SEED{i:02d}", "user_id": "u_ada",
        "restaurant_id": "r_busy", "table_id": f"k_{i}", "starts_at_local": T19, "party_size": 2,
    } for i in range(1, n + 1)]
    return fx


def k0_holders(api):
    refs = []
    for who in ("ada", "bob"):
        for x in api.list_res(who).json()["reservations"]:
            if x["table_id"] == "k_0" and x["status"] == "confirmed":
                refs.append(x["reference"])
    return refs


def test_concurrent_creates_same_slot_one_winner(api):
    """S1-R01 S1-R34 S1-R67"""
    hdr = {"Authorization": api.auth("ada")["Authorization"]}
    body = {"restaurant_id": "r_busy", "table_id": "k_0", "starts_at_local": T19, "party_size": 2}
    n = 20
    results = run_concurrently(n, lambda i, c: c.post("/reservations", json=body,
                                                      headers={**hdr, "Idempotency-Key": f"race-{i}"}))
    codes = [r.status_code for r in results]
    assert codes.count(201) == 1, codes
    for r in results:
        if r.status_code != 201:
            assert_error(r, 409, "table_unavailable")
    assert len(k0_holders(api)) == 1


def test_concurrent_overlapping_different_starts_one_winner(api):
    """S1-R01 S1-R02: 19:00, 19:30, 20:00 all pairwise overlap on one table."""
    hdrs = [api.auth(w) for w in ("ada", "bob", "ada", "bob", "ada", "bob")]
    starts = ["19:00", "19:30", "20:00", "19:00", "19:30", "20:00"]
    results = run_concurrently(6, lambda i, c: c.post("/reservations", json={
        "restaurant_id": "r_busy", "table_id": "k_0", "starts_at_local": f"{THU}T{starts[i]}",
        "party_size": 2}, headers={**hdrs[i], "Idempotency-Key": key()}))
    codes = [r.status_code for r in results]
    assert codes.count(201) == 1, codes
    assert len(k0_holders(api)) == 1


def test_concurrent_patches_onto_same_table_one_winner(api):
    """S1-R01 S1-R84 S1-R85"""
    n = 10
    api.reset(seeded(n))
    hdr = api.auth("ada")
    results = run_concurrently(n, lambda i, c: c.patch(f"/reservations/SEED{i + 1:02d}",
                                                       json={"table_id": "k_0"}, headers=hdr))
    codes = [r.status_code for r in results]
    assert codes.count(200) == 1, codes
    for r in results:
        if r.status_code != 200:
            assert_error(r, 409, "table_unavailable")
    assert len(k0_holders(api)) == 1
    # losers kept their original tables
    tables = sorted(x["table_id"] for x in api.list_res("ada").json()["reservations"])
    assert len(tables) == n and len(set(tables)) == n


def test_concurrent_moves_onto_same_table_one_winner(api):
    """S1-R01 S1-R111 S1-R113"""
    n = 10
    api.reset(seeded(n))
    hdr = api.auth("ada")
    results = run_concurrently(n, lambda i, c: c.post(
        "/reservation-moves", json={"moves": [{"reference": f"SEED{i + 1:02d}", "table_id": "k_0"}]},
        headers={**hdr, "Idempotency-Key": f"mv-{i}"}))
    codes = [r.status_code for r in results]
    assert codes.count(201) == 1, codes
    for r in results:
        if r.status_code != 201:
            assert_error(r, 409, "table_unavailable")
    assert len(k0_holders(api)) == 1


def test_concurrent_move_and_create_race(api):
    """S1-R01: a batch move and plain creates competing for k_0."""
    api.reset(seeded(2))
    hdr = api.auth("ada")

    def go(i, c):
        if i == 0:
            return c.post("/reservation-moves", json={"moves": [{"reference": "SEED01", "table_id": "k_0"},
                                                                {"reference": "SEED02", "table_id": "k_1"}]},
                          headers={**hdr, "Idempotency-Key": "mv-x"})
        return c.post("/reservations", json={"restaurant_id": "r_busy", "table_id": "k_0",
                                             "starts_at_local": f"{THU}T19:30", "party_size": 2},
                      headers={**hdr, "Idempotency-Key": f"cr-{i}"})

    results = run_concurrently(8, go)
    assert sum(r.status_code == 201 for r in results) == 1, [r.status_code for r in results]
    assert all(r.status_code < 500 for r in results)
    assert len(k0_holders(api)) == 1


def test_concurrent_cancel_and_rebook(api):
    """S1-R76 S1-R01: once cancelled, exactly one of the racing re-bookers wins."""
    res = api.booked("ada", "r_busy", "k_0", T19)
    assert api.cancel("ada", res["reference"]).status_code == 200
    hdr = api.auth("bob")
    results = run_concurrently(10, lambda i, c: c.post("/reservations", json={
        "restaurant_id": "r_busy", "table_id": "k_0", "starts_at_local": T19, "party_size": 2},
        headers={**hdr, "Idempotency-Key": key()}))
    assert [r.status_code for r in results].count(201) == 1
    assert len(k0_holders(api)) == 1


def test_fifty_concurrent_reads_within_limits(api):
    """S1-R07 S1-R34"""
    def go(i, c):
        t0 = time.monotonic()
        r = c.get("/availability", params={"restaurant_id": "r_busy", "date": THU, "party_size": "2"})
        return r.status_code, time.monotonic() - t0

    results = run_concurrently(50, go)
    assert all(code == 200 for code, _ in results), results
    assert max(t for _, t in results) < 5.0


def test_reset_within_ten_seconds(client):
    """S1-R08"""
    t0 = time.monotonic()
    r = client.post("/_test/reset", json=standard_fixture())
    assert r.status_code == 204
    assert time.monotonic() - t0 < 10.0
