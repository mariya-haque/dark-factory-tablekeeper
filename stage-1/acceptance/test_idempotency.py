"""Idempotency (spec §7) on POST /reservations and POST /reservation-moves."""
import json

from conftest import THU, assert_error, key, run_concurrently

BODY = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": f"{THU}T19:00", "party_size": 2}


def post_raw(api, who, idem, raw: bytes, path="/reservations"):
    headers = {"Authorization": api.auth(who)["Authorization"], "Content-Type": "application/json"}
    if idem is not None:
        headers["Idempotency-Key"] = idem
    return api.c.post(path, content=raw, headers=headers)


def post(api, who, idem, body=BODY, path="/reservations"):
    return post_raw(api, who, idem, json.dumps(body).encode(), path)


def test_missing_key(api):
    """S1-R45"""
    assert_error(post(api, "ada", None), 400, "missing_idempotency_key")
    assert api.list_res("ada").json() == {"reservations": []}


def test_empty_key(api):
    """S1-R45"""
    assert_error(post(api, "ada", ""), 400, "missing_idempotency_key")


def test_missing_key_on_moves(api):
    """S1-R45 S1-R102"""
    res = api.booked("ada", "r_anker", "t_2", f"{THU}T19:00")
    r = post(api, "ada", None, {"moves": [{"reference": res["reference"], "table_id": "t_3"}]},
             path="/reservation-moves")
    assert_error(r, 400, "missing_idempotency_key")


def test_key_length_bounds(api):
    """S1-R33"""
    assert_error(post(api, "ada", "x" * 256), 422, "validation_failed")
    assert post(api, "ada", "y" * 255).status_code == 201
    assert post(api, "ada", "z", {**BODY, "table_id": "t_3"}).status_code == 201


def test_replay_returns_200_identical_body(api):
    """S1-R49 S1-R50 S1-R03"""
    k = key()
    first = post(api, "ada", k)
    assert first.status_code == 201, first.text
    again = post(api, "ada", k)
    assert again.status_code == 200, again.text
    assert again.json() == first.json()
    assert len(api.list_res("ada").json()["reservations"]) == 1


def test_replay_ignores_key_order_and_whitespace(api):
    """S1-R53"""
    k = key()
    first = post_raw(api, "ada", k, b'{"restaurant_id":"r_anker","table_id":"t_2",'
                                    b'"starts_at_local":"' + THU.encode() + b'T19:00","party_size":2}')
    assert first.status_code == 201, first.text
    again = post_raw(api, "ada", k, b'{\n  "party_size" : 2,\n  "starts_at_local" : "' + THU.encode() +
                     b'T19:00",\n  "table_id":"t_2",   "restaurant_id": "r_anker"\n}\n')
    assert again.status_code == 200, again.text
    assert again.json() == first.json()


def test_same_key_different_body_409(api):
    """S1-R51"""
    k = key()
    assert post(api, "ada", k).status_code == 201
    assert_error(post(api, "ada", k, {**BODY, "party_size": 3}), 409, "idempotency_key_reuse")
    assert len(api.list_res("ada").json()["reservations"]) == 1


def test_reuse_detected_before_validation(api):
    """S1-R48: a used key with a different, invalid body is 409, not 422/404."""
    k = key()
    assert post(api, "ada", k).status_code == 201
    assert_error(post(api, "ada", k, {**BODY, "party_size": "lots"}), 409, "idempotency_key_reuse")
    assert_error(post(api, "ada", k, {"restaurant_id": "r_nope"}), 409, "idempotency_key_reuse")
    assert_error(post(api, "ada", k, {**BODY, "starts_at_local": f"{THU}T19:07"}), 409, "idempotency_key_reuse")


def test_key_scoped_per_user(api):
    """S1-R46: bob's identical key+body is not ada's replay; it is a fresh request."""
    k = key()
    assert post(api, "ada", k).status_code == 201
    assert_error(post(api, "bob", k), 409, "table_unavailable")
    r = post(api, "bob", k, {**BODY, "table_id": "t_3"})
    assert r.status_code == 201, r.text


def test_key_after_4xx_is_first_use(api):
    """S1-R52 S1-R03"""
    api.booked("bob", "r_anker", "t_2", f"{THU}T19:00")
    k = key()
    assert_error(post(api, "ada", k), 409, "table_unavailable")
    r = post(api, "ada", k, {**BODY, "table_id": "t_3"})
    assert r.status_code == 201, r.text
    assert post(api, "ada", k, {**BODY, "table_id": "t_3"}).status_code == 200


def test_key_after_422_same_body_retried(api):
    """S1-R52: the same failing body with the same key is evaluated again (not replayed as 201/200)."""
    k = key()
    bad = {**BODY, "starts_at_local": f"{THU}T19:10"}
    assert_error(post(api, "ada", k, bad), 422, "not_on_slot_grid")
    assert_error(post(api, "ada", k, bad), 422, "not_on_slot_grid")
    assert post(api, "ada", k).status_code == 201


def test_same_key_different_path_is_not_a_replay(api):
    """S1-R47"""
    k = key()
    res = post(api, "ada", k).json()
    r = post(api, "ada", k, {"moves": [{"reference": res["reference"], "table_id": "t_3"}]},
             path="/reservation-moves")
    assert r.status_code == 201, r.text


def test_replay_after_cancel_returns_original_without_effect(api):
    """S1-R55"""
    k = key()
    first = post(api, "ada", k)
    ref = first.json()["reference"]
    assert api.cancel("ada", ref).status_code == 200
    again = post(api, "ada", k)
    assert again.status_code == 200
    assert again.json() == first.json()
    assert again.json()["status"] == "confirmed"
    assert api.get_res("ada", ref).json()["status"] == "cancelled"
    assert "t_2" in api.free_tables("r_anker", f"{THU}T19:00")
    assert len(api.list_res("ada").json()["reservations"]) == 1


def test_replay_after_patch_returns_original(api):
    """S1-R55"""
    k = key()
    first = post(api, "ada", k)
    ref = first.json()["reference"]
    assert api.patch("ada", ref, {"table_id": "t_3"}).status_code == 200
    again = post(api, "ada", k)
    assert again.status_code == 200 and again.json() == first.json()
    assert api.get_res("ada", ref).json()["table_id"] == "t_3"
    assert "t_2" in api.free_tables("r_anker", f"{THU}T19:00")


def test_concurrent_identical_requests_one_effect(api):
    """S1-R54 S1-R03 S1-R34"""
    k = key()
    hdr = {**api.auth("ada", k)}
    n = 20
    results = run_concurrently(n, lambda i, c: c.post("/reservations", json=BODY, headers=hdr))
    codes = [r.status_code for r in results]
    assert codes.count(201) == 1, codes
    assert codes.count(200) == n - 1, codes
    bodies = {json.dumps(r.json(), sort_keys=True) for r in results}
    assert len(bodies) == 1
    assert len(api.list_res("ada").json()["reservations"]) == 1
