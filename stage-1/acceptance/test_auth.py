"""Authentication (spec §6) and the bearer-token rule."""
import pytest

from conftest import THU, assert_error, key, run_concurrently


def signup(client, email="new@example.com", password="12345678", name="Nia"):
    return client.post("/auth/signup", json={"email": email, "password": password, "display_name": name})


def test_signup_returns_201_with_token(api, client):
    """S1-R36 S1-R42"""
    r = signup(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert isinstance(body["user_id"], str) and body["user_id"]
    assert body["display_name"] == "Nia"
    assert isinstance(body["token"], str) and body["token"]
    r = client.get("/reservations", headers={"Authorization": f"Bearer {body['token']}"})
    assert r.status_code == 200 and r.json() == {"reservations": []}


def test_login_returns_200_same_shape(api, client):
    """S1-R37"""
    s = signup(client).json()
    r = client.post("/auth/login", json={"email": "new@example.com", "password": "12345678"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == s["user_id"]
    assert body["display_name"] == "Nia"
    assert isinstance(body["token"], str) and body["token"]


def test_email_taken(api, client):
    """S1-R38"""
    assert signup(client).status_code == 201
    assert_error(signup(client, name="Other"), 409, "email_taken")
    assert_error(signup(client, email="ada@example.com"), 409, "email_taken")  # seeded user


def test_password_length_boundary(api, client):
    """S1-R39"""
    assert_error(signup(client, email="p7@example.com", password="1234567"), 422, "validation_failed")
    assert signup(client, email="p8@example.com", password="12345678").status_code == 201


@pytest.mark.parametrize("email", ["no-at-sign", "@example.com", "local@", ""])
def test_invalid_email_rejected(api, client, email):
    """S1-R40"""
    assert_error(signup(client, email=email), 422, "validation_failed")


@pytest.mark.parametrize("missing", ["email", "password"])
def test_signup_missing_field(api, client, missing):
    """S1-R28"""
    body = {"email": "m@example.com", "password": "12345678", "display_name": "M"}
    del body[missing]
    assert_error(client.post("/auth/signup", json=body), 422, "validation_failed")


def test_signup_wrong_json_type(api, client):
    """S1-R25"""
    r = client.post("/auth/signup", json={"email": 12345, "password": "12345678", "display_name": "N"})
    assert_error(r, 400, "malformed_request")


def test_signup_unparseable_body(api, client):
    """S1-R25"""
    r = client.post("/auth/signup", content=b'{"email": "x@y.z", ',
                    headers={"Content-Type": "application/json"})
    assert_error(r, 400, "malformed_request")


def test_login_failures_are_401(api, client):
    """S1-R41"""
    assert_error(client.post("/auth/login", json={"email": "ada@example.com", "password": "wrong password"}),
                 401, "unauthenticated")
    assert_error(client.post("/auth/login", json={"email": "ghost@example.com", "password": "whatever1"}),
                 401, "unauthenticated")


PROTECTED = [
    ("GET", "/reservations", None),
    ("GET", "/reservations/ABCDEF", None),
    ("POST", "/reservations", {"restaurant_id": "r_anker", "table_id": "t_2",
                               "starts_at_local": f"{THU}T19:00", "party_size": 2}),
    ("POST", "/reservations/ABCDEF/cancel", None),
    ("PATCH", "/reservations/ABCDEF", {"party_size": 2}),
    ("POST", "/reservation-moves", {"moves": [{"reference": "ABCDEF", "table_id": "t_1"}]}),
]


@pytest.mark.parametrize("method,path,body", PROTECTED)
@pytest.mark.parametrize("authz", [None, "Bearer", "Bearer not-a-real-token", "Token abc", "Basic YTpi"])
def test_protected_endpoints_require_valid_bearer(api, client, method, path, body, authz):
    """S1-R26 S1-R42 S1-R102"""
    headers = {"Idempotency-Key": key()}
    if authz is not None:
        headers["Authorization"] = authz
    r = client.request(method, path, json=body, headers=headers)
    assert_error(r, 401, "unauthenticated")


def test_public_endpoints_need_no_token(api, client):
    """S1-R42 S1-R56 S1-R57 S1-R58"""
    assert client.get("/restaurants").status_code == 200
    assert client.get("/restaurants/r_anker").status_code == 200
    assert api.availability("r_anker", THU, 2).status_code == 200
    assert client.get("/health").status_code == 200


def test_multiple_tokens_all_valid(api, client):
    """S1-R43"""
    t0 = signup(client).json()["token"]
    t1 = client.post("/auth/login", json={"email": "new@example.com", "password": "12345678"}).json()["token"]
    t2 = client.post("/auth/login", json={"email": "new@example.com", "password": "12345678"}).json()["token"]
    for t in (t0, t1, t2):
        r = client.get("/reservations", headers={"Authorization": f"Bearer {t}"})
        assert r.status_code == 200, r.text


def test_token_identifies_the_user(api, client):
    """S1-R43 S1-R73: a reservation made with one token is visible with another of the same user."""
    t1 = api.login("ada@example.com", "correct horse").json()["token"]
    t2 = api.login("ada@example.com", "correct horse").json()["token"]
    r = client.post("/reservations", json={"restaurant_id": "r_anker", "table_id": "t_2",
                                           "starts_at_local": f"{THU}T19:00", "party_size": 2},
                    headers={"Authorization": f"Bearer {t1}", "Idempotency-Key": key()})
    assert r.status_code == 201
    ref = r.json()["reference"]
    r = client.get(f"/reservations/{ref}", headers={"Authorization": f"Bearer {t2}"})
    assert r.status_code == 200


def test_concurrent_signups_same_email_exactly_one_wins(api):
    """S1-R38 S1-R34 (uniqueness under concurrency)"""
    n = 10
    results = run_concurrently(n, lambda i, c: c.post("/auth/signup", json={
        "email": "race@example.com", "password": f"password{i}", "display_name": f"R{i}"}))
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 1, codes
    for r in results:
        if r.status_code != 201:
            assert_error(r, 409, "email_taken")


def test_export_holds_no_plaintext_password(api, client):
    """S1-R44: passwords are hashed; a state snapshot must not contain the plaintext."""
    assert signup(client, password="Zebra-Quartz-9931").status_code == 201
    text = client.get("/_test/export").text
    assert "Zebra-Quartz-9931" not in text
    assert "correct horse" not in text
