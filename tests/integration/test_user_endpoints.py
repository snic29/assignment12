"""
API tests for /users/register and /users/login, verifying the resulting
database state directly through the test database session.
"""
from uuid import UUID

import pytest

from app.models.user import User
from tests.conftest import make_user_payload


def _get_user(db_session, username: str):
    db_session.expire_all()  # make sure we see what the API committed
    return db_session.query(User).filter(User.username == username).first()


def _error_messages(response) -> str:
    """Flatten a 422 validation error body into one string for assertions."""
    body = response.json()
    assert "detail" in body
    return " ".join(err["msg"] for err in body["detail"])


# --------------------------------------------------------------------------
# Register
# --------------------------------------------------------------------------
def test_register_creates_user_in_db(client, db_session):
    payload = make_user_payload("reg", first_name="Ada", last_name="Lovelace")
    response = client.post("/users/register", json=payload)
    assert response.status_code == 201, response.text
    body = response.json()

    user = _get_user(db_session, payload["username"])
    assert user is not None
    assert str(user.id) == body["id"]
    assert user.email == payload["email"]
    assert user.first_name == "Ada"
    assert user.last_name == "Lovelace"
    assert user.is_active is True
    assert user.is_verified is False
    assert user.created_at is not None
    assert user.last_login is None


def test_register_stores_hashed_password(client, db_session):
    payload = make_user_payload("hash")
    assert client.post("/users/register", json=payload).status_code == 201

    user = _get_user(db_session, payload["username"])
    assert user.password != payload["password"]
    assert user.password.startswith("$2")  # bcrypt hash prefix
    assert user.verify_password(payload["password"]) is True
    assert user.verify_password("WrongPass123!") is False


def test_register_response_hides_password(client):
    body = client.post("/users/register", json=make_user_payload("hide")).json()
    assert "password" not in body
    assert "confirm_password" not in body
    UUID(body["id"])  # valid UUID


def test_register_duplicate_username_returns_400_and_no_new_row(client, db_session):
    first = make_user_payload("dupname")
    assert client.post("/users/register", json=first).status_code == 201

    second = make_user_payload("other", username=first["username"])
    response = client.post("/users/register", json=second)
    assert response.status_code == 400
    assert response.json() == {"detail": "Username or email already exists"}

    db_session.expire_all()
    assert db_session.query(User).filter(User.username == first["username"]).count() == 1
    assert db_session.query(User).filter(User.email == second["email"]).count() == 0


def test_register_duplicate_email_returns_400(client):
    first = make_user_payload("dupmail")
    assert client.post("/users/register", json=first).status_code == 201
    response = client.post("/users/register", json=make_user_payload("x", email=first["email"]))
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


@pytest.mark.parametrize(
    "overrides, expected_message",
    [
        ({"confirm_password": "Different123!"}, "Passwords do not match"),
        ({"password": "short1!", "confirm_password": "short1!"}, "at least 8 characters"),
        ({"password": "nouppercase1!", "confirm_password": "nouppercase1!"}, "uppercase"),
        ({"password": "NOLOWERCASE1!", "confirm_password": "NOLOWERCASE1!"}, "lowercase"),
        ({"password": "NoDigitsHere!", "confirm_password": "NoDigitsHere!"}, "digit"),
        ({"password": "NoSpecial123", "confirm_password": "NoSpecial123"}, "special character"),
        ({"email": "not-an-email"}, "email"),
        ({"username": "ab"}, "at least 3 characters"),
    ],
)
def test_register_invalid_data_returns_422_and_no_row(client, db_session, overrides, expected_message):
    payload = make_user_payload("bad", **overrides)
    response = client.post("/users/register", json=payload)
    assert response.status_code == 422
    assert expected_message.lower() in _error_messages(response).lower()

    db_session.expire_all()
    assert db_session.query(User).filter(User.username == payload["username"]).count() == 0


@pytest.mark.parametrize("missing", ["first_name", "last_name", "email", "username", "password", "confirm_password"])
def test_register_missing_field_returns_422(client, missing):
    payload = make_user_payload("missing")
    del payload[missing]
    response = client.post("/users/register", json=payload)
    assert response.status_code == 422
    locs = [err["loc"] for err in response.json()["detail"]]
    assert any(missing in loc for loc in locs)


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------
def test_login_returns_tokens_and_updates_last_login(client, db_session):
    payload = make_user_payload("login")
    assert client.post("/users/register", json=payload).status_code == 201
    assert _get_user(db_session, payload["username"]).last_login is None

    response = client.post("/users/login", json={"username": payload["username"], "password": payload["password"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]

    user = _get_user(db_session, payload["username"])
    assert body["user_id"] == str(user.id)
    assert user.last_login is not None


def test_login_token_identifies_user(client, db_session):
    payload = make_user_payload("tokid")
    client.post("/users/register", json=payload)
    token = client.post(
        "/users/login", json={"username": payload["username"], "password": payload["password"]}
    ).json()["access_token"]

    user = _get_user(db_session, payload["username"])
    assert User.verify_token(token) == user.id

    me = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["id"] == str(user.id)


def test_login_with_email(client):
    payload = make_user_payload("email")
    client.post("/users/register", json=payload)
    response = client.post("/users/login", json={"username": payload["email"], "password": payload["password"]})
    assert response.status_code == 200
    assert response.json()["username"] == payload["username"]


def test_login_wrong_password_returns_401_and_keeps_last_login_empty(client, db_session):
    payload = make_user_payload("wrong")
    client.post("/users/register", json=payload)
    response = client.post("/users/login", json={"username": payload["username"], "password": "WrongPass123!"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid username or password"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert _get_user(db_session, payload["username"]).last_login is None


def test_login_unknown_user_returns_401(client):
    response = client.post("/users/login", json={"username": "ghostuser", "password": "SecurePass123!"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"


@pytest.mark.parametrize(
    "body",
    [
        {"username": "someone"},                          # missing password
        {"password": "SecurePass123!"},                   # missing username
        {"username": "ab", "password": "SecurePass123!"}, # username too short
        {"username": "someone", "password": "short"},     # password too short
    ],
)
def test_login_invalid_payload_returns_422(client, body):
    response = client.post("/users/login", json=body)
    assert response.status_code == 422
    assert "detail" in response.json()


def test_users_me_without_token_returns_401(client):
    response = client.get("/users/me")
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_users_me_with_bad_token_returns_401(client):
    response = client.get("/users/me", headers={"Authorization": "Bearer garbage"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"
