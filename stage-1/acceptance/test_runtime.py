"""Runtime contract, reset/seed and cross-cutting conventions (spec §3, §4, §5)."""
from conftest import (
    PAST_THU, THU, assert_error, assert_reservation, key, standard_fixture,
)


def test_health_ok(client):
    """S1-R10"""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_reset_returns_204_without_auth(client):
    """S1-R11"""
    r = client.post("/_test/reset", json=standard_fixture())
    assert r.status_code == 204
    r = client.post("/_test/reset", json=standard_fixture())  # repeatable
    assert r.status_code == 204


def test_reset_replaces_all_state(api, client):
    """S1-R11 S1-R12: after reset only the new fixture is visible."""
    tok = api.token("ada")
    res = api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    used_key = key()
    assert api.book("ada", "r_anker", "t_1", f"{THU}T19:00", idem=used_key).status_code == 201

    fixture = standard_fixture()
    fixture["restaurants"] = [r for r in fixture["restaurants"] if r["id"] != "r_grid"]
    api.reset(fixture)

    ids = {r["id"] for r in client.get("/restaurants").json()["restaurants"]}
    assert "r_grid" not in ids and "r_anker" in ids
    assert_error(client.get("/restaurants/r_grid"), 404, "not_found")
    # old token is gone with the old state
    assert_error(client.get("/reservations", headers={"Authorization": f"Bearer {tok}"}), 401, "unauthenticated")
    # old reservations are gone and the table is free again
    assert api.list_res("ada").json() == {"reservations": []}
    assert_error(api.get_res("ada", res["reference"]), 404, "not_found")
    assert "t_2" in api.free_tables("r_anker", f"{THU}T19:00")
    # old idempotency keys are gone: a different body with the same key is a first use
    r = api.book("ada", "r_anker", "t_3", f"{THU}T20:00", idem=used_key)
    assert r.status_code == 201, r.text


def test_reset_with_empty_fixture(api, client):
    """S1-R11 S1-R12"""
    api.reset({"users": [], "restaurants": [], "reservations": []})
    assert client.get("/restaurants").json() == {"restaurants": []}
    assert_error(api.login("ada@example.com", "correct horse"), 401, "unauthenticated")


def test_seeded_users_can_log_in_immediately(api):
    """S1-R21 S1-R37"""
    r = api.login("ada@example.com", "correct horse")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == "u_ada"
    assert body["display_name"] == "Ada"
    assert isinstance(body["token"], str) and body["token"]


def test_seeded_reservation_is_confirmed_owned_and_occupies(api):
    """S1-R22 S1-R65 S1-R67"""
    fixture = standard_fixture()
    fixture["reservations"] = [{
        "id": "res_seed1", "reference": "SEED01", "user_id": "u_ada",
        "restaurant_id": "r_anker", "table_id": "t_2",
        "starts_at_local": f"{THU}T19:00", "party_size": 3,
    }]
    api.reset(fixture)
    r = api.get_res("ada", "SEED01")
    assert r.status_code == 200, r.text
    assert_reservation(r.json(), reservation_id="res_seed1", reference="SEED01",
                       restaurant_id="r_anker", table_id="t_2", party_size=3,
                       status="confirmed", starts_at_local=f"{THU}T19:00",
                       starts_at=f"{THU}T19:00:00+02:00", ends_at=f"{THU}T20:30:00+02:00")
    listed = api.list_res("ada").json()["reservations"]
    assert [x["reference"] for x in listed] == ["SEED01"]
    assert_error(api.get_res("bob", "SEED01"), 404, "not_found")
    assert "t_2" not in api.free_tables("r_anker", f"{THU}T19:30")
    assert_error(api.book("bob", "r_anker", "t_2", f"{THU}T20:00"), 409, "table_unavailable")


def test_64_character_fixture_ids_accepted(api, client):
    """S1-R17"""
    rid, tid, uid = "r" * 64, "t" * 64, "u" * 64
    fixture = standard_fixture()
    fixture["users"].append({"id": uid, "email": "long@example.com", "password": "longlonglong",
                             "display_name": "Long"})
    fixture["restaurants"].append({
        "id": rid, "name": "Long Id", "timezone": "Europe/Berlin", "slot_minutes": 30,
        "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
        "opening_hours": [{"weekday": "thu", "opens": "18:00", "closes": "23:00"}],
        "tables": [{"id": tid, "label": "L", "capacity": 4}],
    })
    api.reset(fixture)
    assert client.get(f"/restaurants/{rid}").status_code == 200
    login = api.login("long@example.com", "longlonglong")
    assert login.status_code == 200 and login.json()["user_id"] == uid
    tok = login.json()["token"]
    r = client.post("/reservations", json={"restaurant_id": rid, "table_id": tid,
                                           "starts_at_local": f"{THU}T19:00", "party_size": 2},
                    headers={"Authorization": f"Bearer {tok}", "Idempotency-Key": key()})
    assert r.status_code == 201, r.text
    assert_reservation(r.json(), restaurant_id=rid, table_id=tid)


def test_generated_ids_at_most_64_chars(api):
    """S1-R17"""
    r = api.c.post("/auth/signup", json={"email": "new@example.com", "password": "12345678",
                                         "display_name": "New"})
    assert r.status_code == 201
    assert isinstance(r.json()["user_id"], str) and 0 < len(r.json()["user_id"]) <= 64
    res = api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    assert 0 < len(res["reservation_id"]) <= 64


def test_json_content_type_with_utf8_charset(api, client):
    """S1-R13"""
    for r in (client.get("/health"), client.get("/restaurants"),
              client.get("/restaurants/nope"),
              api.availability("r_anker", THU, 2)):
        ctype = r.headers.get("content-type", "").lower().replace(" ", "")
        assert ctype.startswith("application/json"), ctype
        assert "charset=utf-8" in ctype, ctype


def test_created_at_and_offsets_rfc3339(api):
    """S1-R14"""
    res = api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    assert_reservation(res)  # checks starts_at, ends_at, created_at are RFC 3339 with offset
    assert res["starts_at"].endswith("+02:00") and res["ends_at"].endswith("+02:00")


def test_unknown_body_fields_ignored(api, client):
    """S1-R15"""
    body = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": f"{THU}T19:00",
            "party_size": 2, "colour": "blue", "notes": {"x": [1, 2]}}
    r = client.post("/reservations", json=body, headers=api.auth("ada", key()))
    assert r.status_code == 201, r.text
    r = client.post("/auth/signup", json={"email": "z@example.com", "password": "12345678",
                                          "display_name": "Z", "extra": True})
    assert r.status_code == 201, r.text


def test_unknown_query_params_ignored(api):
    """S1-R16"""
    r = api.availability("r_anker", THU, 2, utm_source="mail", foo="bar")
    assert r.status_code == 200, r.text


def test_unknown_route_has_error_body(client):
    """S1-R24 S1-R27"""
    assert_error(client.get("/definitely-not-a-route"), 404, "not_found")


def test_wrong_method_has_error_body(client):
    """S1-R24"""
    r = client.delete("/restaurants")
    assert 400 <= r.status_code < 500
    assert_error(r, r.status_code)


def test_past_booking_not_rejected(api):
    """S1-R23: a start in the past is not by itself a reason to reject."""
    r = api.book("ada", "r_anker", "t_2", f"{PAST_THU}T19:00")
    assert r.status_code == 201, r.text
    assert_reservation(r.json(), starts_at=f"{PAST_THU}T19:00:00+02:00", status="confirmed")
