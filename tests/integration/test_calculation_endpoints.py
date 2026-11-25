"""
API tests for the /calculations BREAD endpoints, verifying the resulting
database state directly through the test database session.
"""
from uuid import UUID, uuid4

import pytest

from app.models.calculation import Calculation, Division
from app.models.user import User
from tests.conftest import make_user_payload


def _get_calc(db_session, calc_id: str):
    db_session.expunge_all()  # drop cached objects so we load what the API committed
    return db_session.query(Calculation).filter(Calculation.id == UUID(calc_id)).first()


def _create(client, headers, calc_type="addition", inputs=(1, 2)):
    response = client.post("/calculations", json={"type": calc_type, "inputs": list(inputs)}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _error_messages(response) -> str:
    body = response.json()
    assert "detail" in body
    if isinstance(body["detail"], str):
        return body["detail"]
    return " ".join(err["msg"] for err in body["detail"])


# --------------------------------------------------------------------------
# Full lifecycle: create -> retrieve -> update -> delete, checked in the DB
# --------------------------------------------------------------------------
def test_calculation_lifecycle_in_db(client, db_session, auth_headers):
    # Add
    created = _create(client, auth_headers, "multiplication", [3, 4])
    assert created["result"] == 12
    row = _get_calc(db_session, created["id"])
    assert row is not None
    assert row.type == "multiplication"
    assert row.inputs == [3, 4]
    assert row.result == 12
    owner = db_session.query(User).filter(User.id == row.user_id).one()
    assert str(owner.id) == created["user_id"]

    # Read
    read = client.get(f"/calculations/{created['id']}", headers=auth_headers)
    assert read.status_code == 200
    assert read.json() == created

    # Browse
    listed = client.get("/calculations", headers=auth_headers)
    assert listed.status_code == 200
    assert [c["id"] for c in listed.json()] == [created["id"]]

    # Edit (PUT: full replacement)
    updated = client.put(
        f"/calculations/{created['id']}", json={"type": "multiplication", "inputs": [5, 6]}, headers=auth_headers
    )
    assert updated.status_code == 200
    assert updated.json()["result"] == 30
    row = _get_calc(db_session, created["id"])
    assert row.inputs == [5, 6]
    assert row.result == 30
    assert row.updated_at >= row.created_at

    # Edit (PATCH: change only the type, keep inputs)
    patched = client.patch(f"/calculations/{created['id']}", json={"type": "division"}, headers=auth_headers)
    assert patched.status_code == 200
    assert patched.json()["type"] == "division"
    row = _get_calc(db_session, created["id"])
    assert row.type == "division"
    assert isinstance(row, Division)  # reloaded as the right subclass
    assert row.result == pytest.approx(5 / 6)

    # Delete
    deleted = client.delete(f"/calculations/{created['id']}", headers=auth_headers)
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert _get_calc(db_session, created["id"]) is None
    assert client.get(f"/calculations/{created['id']}", headers=auth_headers).status_code == 404


@pytest.mark.parametrize(
    "calc_type, inputs, expected",
    [
        ("addition", [1.5, 2.5, 3], 7),
        ("subtraction", [10, 3, 2], 5),
        ("multiplication", [2, 3, 4], 24),
        ("division", [100, 4, 5], 5),
    ],
)
def test_create_each_type_persists_result(client, db_session, auth_headers, calc_type, inputs, expected):
    created = _create(client, auth_headers, calc_type, inputs)
    assert created["result"] == pytest.approx(expected)
    row = _get_calc(db_session, created["id"])
    assert row.type == calc_type
    assert row.result == pytest.approx(expected)


def test_create_ignores_client_supplied_user_id(client, db_session, auth_headers):
    other_id = str(uuid4())
    response = client.post(
        "/calculations",
        json={"type": "addition", "inputs": [1, 2], "user_id": other_id},
        headers=auth_headers,
    )
    assert response.status_code == 201
    row = _get_calc(db_session, response.json()["id"])
    assert str(row.user_id) != other_id
    assert str(row.user_id) == client.get("/users/me", headers=auth_headers).json()["id"]


# --------------------------------------------------------------------------
# Invalid data -> error status codes and error bodies, nothing written
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "body, expected_message",
    [
        ({"type": "modulus", "inputs": [1, 2]}, "Type must be one of"),
        ({"type": "addition", "inputs": [1]}, "at least 2 items"),
        ({"type": "division", "inputs": [1, 0]}, "Cannot divide by zero"),
        ({"type": "addition", "inputs": "1,2"}, "valid list"),
        ({"type": "addition", "inputs": [1, "two"]}, "valid number"),
        ({"inputs": [1, 2]}, "Field required"),
        ({"type": "addition"}, "Field required"),
    ],
)
def test_create_invalid_data_returns_422_and_writes_nothing(client, db_session, auth_headers, body, expected_message):
    me = client.get("/users/me", headers=auth_headers).json()["id"]
    response = client.post("/calculations", json=body, headers=auth_headers)
    assert response.status_code == 422
    assert expected_message.lower() in _error_messages(response).lower()

    db_session.expire_all()
    assert db_session.query(Calculation).filter(Calculation.user_id == UUID(me)).count() == 0


@pytest.mark.parametrize(
    "body, status_code, expected_message",
    [
        ({"inputs": [10, 0]}, 400, "Cannot divide by zero"),
        ({"inputs": [1]}, 422, "at least 2 items"),
        ({"type": "modulus"}, 422, "Type must be one of"),
        ({"inputs": "nope"}, 422, "valid list"),
    ],
)
def test_patch_invalid_data_returns_error_and_leaves_row_unchanged(
    client, db_session, auth_headers, body, status_code, expected_message
):
    created = _create(client, auth_headers, "division", [10, 2])
    response = client.patch(f"/calculations/{created['id']}", json=body, headers=auth_headers)
    assert response.status_code == status_code
    assert expected_message.lower() in _error_messages(response).lower()

    row = _get_calc(db_session, created["id"])
    assert row.type == "division"
    assert row.inputs == [10, 2]
    assert row.result == 5


@pytest.mark.parametrize(
    "body, expected_message",
    [
        ({"inputs": [3, 4]}, "Field required"),             # type missing
        ({"type": "addition"}, "Field required"),           # inputs missing
        ({}, "Field required"),
        ({"type": "division", "inputs": [10, 0]}, "Cannot divide by zero"),
        ({"type": "addition", "inputs": [1]}, "at least 2 items"),
        ({"type": "modulus", "inputs": [1, 2]}, "Type must be one of"),
    ],
)
def test_put_requires_full_valid_body_and_leaves_row_unchanged(
    client, db_session, auth_headers, body, expected_message
):
    created = _create(client, auth_headers, "division", [10, 2])
    response = client.put(f"/calculations/{created['id']}", json=body, headers=auth_headers)
    assert response.status_code == 422
    assert expected_message.lower() in _error_messages(response).lower()

    row = _get_calc(db_session, created["id"])
    assert row.type == "division"
    assert row.inputs == [10, 2]
    assert row.result == 5


def test_put_replaces_type_and_inputs(client, db_session, auth_headers):
    created = _create(client, auth_headers, "addition", [1, 2])
    response = client.put(
        f"/calculations/{created['id']}", json={"type": "subtraction", "inputs": [10, 4]}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["type"] == "subtraction"
    assert response.json()["result"] == 6

    row = _get_calc(db_session, created["id"])
    assert row.type == "subtraction"
    assert row.inputs == [10, 4]
    assert row.result == 6


def test_patch_inputs_only_keeps_type(client, db_session, auth_headers):
    created = _create(client, auth_headers, "multiplication", [2, 3])
    response = client.patch(f"/calculations/{created['id']}", json={"inputs": [4, 5]}, headers=auth_headers)
    assert response.status_code == 200
    row = _get_calc(db_session, created["id"])
    assert row.type == "multiplication"
    assert row.inputs == [4, 5]
    assert row.result == 20


def test_patch_empty_body_changes_nothing_but_succeeds(client, db_session, auth_headers):
    created = _create(client, auth_headers, "addition", [1, 2])
    response = client.patch(f"/calculations/{created['id']}", json={}, headers=auth_headers)
    assert response.status_code == 200
    row = _get_calc(db_session, created["id"])
    assert row.type == "addition"
    assert row.inputs == [1, 2]
    assert row.result == 3


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
def test_invalid_id_format_returns_400(client, auth_headers, method):
    kwargs = {"json": {"type": "addition", "inputs": [1, 2]}} if method in ("put", "patch") else {}
    response = getattr(client, method)("/calculations/not-a-uuid", headers=auth_headers, **kwargs)
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid calculation id format."}


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
def test_missing_id_returns_404(client, auth_headers, method):
    kwargs = {"json": {"type": "addition", "inputs": [1, 2]}} if method in ("put", "patch") else {}
    response = getattr(client, method)(f"/calculations/{uuid4()}", headers=auth_headers, **kwargs)
    assert response.status_code == 404
    assert response.json() == {"detail": "Calculation not found."}


def test_other_users_calculation_is_404_and_untouched(client, db_session, auth_headers):
    created = _create(client, auth_headers, "addition", [1, 2])

    intruder = make_user_payload("intruder")
    client.post("/users/register", json=intruder)
    token = client.post(
        "/users/login", json={"username": intruder["username"], "password": intruder["password"]}
    ).json()["access_token"]
    intruder_headers = {"Authorization": f"Bearer {token}"}
    url = f"/calculations/{created['id']}"

    assert client.get(url, headers=intruder_headers).status_code == 404
    assert client.put(url, json={"type": "addition", "inputs": [9, 9]}, headers=intruder_headers).status_code == 404
    assert client.patch(url, json={"inputs": [9, 9]}, headers=intruder_headers).status_code == 404
    assert client.delete(url, headers=intruder_headers).status_code == 404
    assert client.get("/calculations", headers=intruder_headers).json() == []

    row = _get_calc(db_session, created["id"])
    assert row is not None
    assert row.inputs == [1, 2]


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/calculations"),
        ("post", "/calculations"),
        ("get", "/calculations/{id}"),
        ("put", "/calculations/{id}"),
        ("patch", "/calculations/{id}"),
        ("delete", "/calculations/{id}"),
    ],
)
def test_endpoints_require_auth(client, method, path):
    kwargs = {"json": {"type": "addition", "inputs": [1, 2]}} if method in ("post", "put", "patch") else {}
    response = getattr(client, method)(path.format(id=uuid4()), **kwargs)
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}
    assert response.headers["www-authenticate"] == "Bearer"
