"""Export and import (spec §10)."""
import json

import pytest

from conftest import (
    THU, assert_error, assert_same_reservation, key, standard_fixture,
)


def export(client):
    r = client.get("/_test/export")
    assert r.status_code == 200, r.text
    return r.json()


def do_import(client, snap):
    return client.post("/_test/import", json=snap)


def test_export_shape_unauthenticated(api, client):
    """S1-R93"""
    snap = export(client)
    assert snap["track"] == "tablekeeper"
    assert snap["format_version"] == 1
    assert isinstance(snap["state"], dict)


def test_round_trip_preserves_everything(api, client):
    """S1-R94 S1-R96 S1-R97 S1-R98 S1-R99 S1-R117"""
    # --- build source state
    signup = client.post("/auth/signup", json={"email": "nia@example.com", "password": "nia-secret-1",
                                               "display_name": "Nia"}).json()
    nia_tok = signup["token"]
    ada_tok = api.token("ada")
    k_ok, k_failed, k_move = key(), key(), key()
    body = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": f"{THU}T19:00", "party_size": 2}
    created = client.post("/reservations", json=body, headers=api.auth("ada", k_ok))
    assert created.status_code == 201
    other = api.booked("ada", "r_anker", "t_1", f"{THU}T21:00")
    assert api.cancel("ada", other["reference"]).status_code == 200
    failed_body = {**body, "starts_at_local": f"{THU}T19:30"}
    assert client.post("/reservations", json=failed_body,
                       headers=api.auth("ada", k_failed)).status_code == 409
    moves = [{"reference": created.json()["reference"], "party_size": 3}]
    moved = api.moves("ada", moves, idem=k_move)
    assert moved.status_code == 201
    before = api.list_res("ada").json()["reservations"]
    restaurant_before = client.get("/restaurants/r_anker").json()

    snap = export(client)
    snap_text = json.dumps(snap, sort_keys=True)

    # --- writes after the export must not leak into the snapshot
    late = api.booked("ada", "r_anker", "t_3", f"{THU}T19:00")
    client.post("/auth/signup", json={"email": "late@example.com", "password": "late-pass-1",
                                      "display_name": "Late"})
    late_tok = api.login("ada@example.com", "correct horse").json()["token"]
    assert json.dumps(snap, sort_keys=True) == snap_text

    # --- replace destination state with something else entirely, then import
    api.reset()
    r = do_import(client, snap)
    assert r.status_code == 204, r.text

    # accounts, hashed-password login and existing tokens
    assert api.login("nia@example.com", "nia-secret-1").status_code == 200
    assert api.login("nia@example.com", "nia-secret-1").json()["user_id"] == signup["user_id"]
    assert client.get("/reservations", headers={"Authorization": f"Bearer {nia_tok}"}).status_code == 200
    ada_h = {"Authorization": f"Bearer {ada_tok}"}
    after = client.get("/reservations", headers=ada_h).json()["reservations"]
    assert len(after) == len(before)
    for x, y in zip(after, before):
        assert_same_reservation(x, y)
    # post-export data and credentials are gone
    assert_error(client.get(f"/reservations/{late['reference']}", headers=ada_h), 404, "not_found")
    assert_error(api.login("late@example.com", "late-pass-1"), 401, "unauthenticated")
    assert_error(client.get("/reservations", headers={"Authorization": f"Bearer {late_tok}"}),
                 401, "unauthenticated")
    # fixture configuration
    assert client.get("/restaurants/r_anker").json() == restaurant_before
    # occupancy restored
    free = api.free_tables("r_anker", f"{THU}T19:00")
    assert "t_2" not in free and "t_3" in free and "t_1" in free
    # idempotent receipts
    h = {**ada_h, "Idempotency-Key": k_ok}
    replay = client.post("/reservations", json=body, headers=h)
    assert replay.status_code == 200 and replay.json() == created.json()
    replay_m = client.post("/reservation-moves", json={"moves": moves}, headers={**ada_h, "Idempotency-Key": k_move})
    assert replay_m.status_code == 200 and replay_m.json() == moved.json()
    # failed keys remain reusable
    ok = client.post("/reservations", json={**body, "table_id": "t_3"}, headers={**ada_h, "Idempotency-Key": k_failed})
    assert ok.status_code == 201, ok.text
    # references are still unique after import
    refs = [x["reference"] for x in client.get("/reservations", headers=ada_h).json()["reservations"]]
    assert len(refs) == len(set(refs))


def test_import_is_replacement_and_repeatable(api, client):
    """S1-R94 S1-R99"""
    api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    snap = export(client)
    assert do_import(client, snap).status_code == 204
    assert do_import(client, snap).status_code == 204
    assert len(api.list_res("ada").json()["reservations"]) == 1
    ids = [x["id"] for x in client.get("/restaurants").json()["restaurants"]]
    assert len(ids) == len(set(ids)) == len(standard_fixture()["restaurants"])


def test_import_removes_destination_data(api, client):
    """S1-R99"""
    empty = {"users": [], "restaurants": [], "reservations": []}
    api.reset(empty)
    snap = export(client)
    api.reset()
    tok = api.token("ada")
    api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    assert do_import(client, snap).status_code == 204
    assert client.get("/restaurants").json() == {"restaurants": []}
    assert_error(client.get("/reservations", headers={"Authorization": f"Bearer {tok}"}), 401, "unauthenticated")
    assert_error(api.login("ada@example.com", "correct horse"), 401, "unauthenticated")


@pytest.mark.parametrize("mutate", [
    lambda s: {**s, "track": "other"},
    lambda s: {**s, "format_version": 2},
    lambda s: {k: v for k, v in s.items() if k != "state"},
    lambda s: {k: v for k, v in s.items() if k != "track"},
    lambda s: {k: v for k, v in s.items() if k != "format_version"},
    lambda s: {**s, "state": {"bogus": True}},
    lambda s: {},
])
def test_invalid_import_is_422_and_changes_nothing(api, client, mutate):
    """S1-R95"""
    snap = export(client)
    res = api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    tok = api.token("ada")
    assert_error(client.post("/_test/import", json=mutate(snap)), 422, "validation_failed")
    r = client.get(f"/reservations/{res['reference']}", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200


def test_unparseable_import_is_400(api, client):
    """S1-R95 S1-R25"""
    res = api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    r = client.post("/_test/import", content=b'{"track": "tablekeeper", ',
                    headers={"Content-Type": "application/json"})
    assert_error(r, 400, "malformed_request")
    assert api.get_res("ada", res["reference"]).status_code == 200


def test_reset_clears_imported_state(api, client):
    """S1-R100"""
    client.post("/auth/signup", json={"email": "imp@example.com", "password": "imported-1", "display_name": "I"})
    snap = export(client)
    api.reset()
    assert do_import(client, snap).status_code == 204
    assert api.login("imp@example.com", "imported-1").status_code == 200
    api.reset()
    assert_error(api.login("imp@example.com", "imported-1"), 401, "unauthenticated")
