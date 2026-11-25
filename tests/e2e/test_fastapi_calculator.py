from datetime import datetime, timezone
from uuid import uuid4
import pytest
import requests

# Import the Calculation model for direct model tests.
from app.models.calculation import Calculation

# ---------------------------------------------------------------------------
# Helper Fixtures and Functions
# ---------------------------------------------------------------------------
@pytest.fixture
def base_url(fastapi_server: str) -> str:
    """
    Returns the FastAPI server base URL without a trailing slash.
    """
    return fastapi_server.rstrip("/")

def _parse_datetime(dt_str: str) -> datetime:
    """Helper function to parse datetime strings from API responses."""
    if dt_str.endswith('Z'):
        dt_str = dt_str.replace('Z', '+00:00')
    return datetime.fromisoformat(dt_str)

def register_and_login(base_url: str, user_data: dict) -> dict:
    """
    Registers a new user and logs in, returning the token response data.
    """
    reg_url = f"{base_url}/users/register"
    login_url = f"{base_url}/users/login"
    
    reg_response = requests.post(reg_url, json=user_data)
    assert reg_response.status_code == 201, f"User registration failed: {reg_response.text}"
    
    login_payload = {
        "username": user_data["username"],
        "password": user_data["password"]
    }
    login_response = requests.post(login_url, json=login_payload)
    assert login_response.status_code == 200, f"Login failed: {login_response.text}"
    return login_response.json()

# ---------------------------------------------------------------------------
# Health and Auth Endpoint Tests
# ---------------------------------------------------------------------------
def test_health_endpoint(base_url: str):
    url = f"{base_url}/health"
    response = requests.get(url)
    assert response.status_code == 200, f"Expected status code 200 but got {response.status_code}. Response: {response.text}"
    assert response.json() == {"status": "ok"}, "Unexpected response from /health."

def test_user_registration(base_url: str):
    url = f"{base_url}/users/register"
    payload = {
        "first_name": "Alice",
        "last_name": "Smith",
        "email": "alice.smith@example.com",
        "username": "alicesmith",
        "password": "SecurePass123!",
        "confirm_password": "SecurePass123!"
    }
    response = requests.post(url, json=payload)
    assert response.status_code == 201, f"Expected 201 but got {response.status_code}. Response: {response.text}"
    data = response.json()
    for key in ["id", "username", "email", "first_name", "last_name", "is_active", "is_verified"]:
        assert key in data, f"Field '{key}' missing in registration response."
    assert data["username"] == "alicesmith"
    assert data["email"] == "alice.smith@example.com"
    assert data["first_name"] == "Alice"
    assert data["last_name"] == "Smith"
    assert data["is_active"] is True
    assert data["is_verified"] is False

def test_user_login(base_url: str):
    reg_url = f"{base_url}/users/register"
    login_url = f"{base_url}/users/login"
    
    test_user = {
        "first_name": "Bob",
        "last_name": "Jones",
        "email": "bob.jones@example.com",
        "username": "bobjones",
        "password": "SecurePass123!",
        "confirm_password": "SecurePass123!"
    }
    
    # Register user
    reg_response = requests.post(reg_url, json=test_user)
    assert reg_response.status_code == 201, f"User registration failed: {reg_response.text}"
    
    # Login user
    login_payload = {
        "username": test_user["username"],
        "password": test_user["password"]
    }
    login_response = requests.post(login_url, json=login_payload)
    assert login_response.status_code == 200, f"Login failed: {login_response.text}"
    
    login_data = login_response.json()
    required_fields = {
        "access_token": str,
        "refresh_token": str,
        "token_type": str,
        "expires_at": str,  # ISO datetime string
        "user_id": str,     # UUID string
        "username": str,
        "email": str,
        "first_name": str,
        "last_name": str,
        "is_active": bool,
        "is_verified": bool
    }
    
    for field, expected_type in required_fields.items():
        assert field in login_data, f"Missing field: {field}"
        assert isinstance(login_data[field], expected_type), f"Field {field} has wrong type. Expected {expected_type}, got {type(login_data[field])}"
    
    assert login_data["token_type"].lower() == "bearer", "Token type should be 'bearer'"
    assert len(login_data["access_token"]) > 0, "Access token should not be empty"
    assert len(login_data["refresh_token"]) > 0, "Refresh token should not be empty"
    assert login_data["username"] == test_user["username"]
    assert login_data["email"] == test_user["email"]
    assert login_data["first_name"] == test_user["first_name"]
    assert login_data["last_name"] == test_user["last_name"]
    assert login_data["is_active"] is True
    
    expires_at = _parse_datetime(login_data["expires_at"])
    current_time = datetime.now(timezone.utc)
    assert expires_at.tzinfo is not None, "expires_at should be timezone-aware"
    assert current_time.tzinfo is not None, "current_time should be timezone-aware"
    assert expires_at > current_time, "Token expiration should be in the future"

def _new_user(prefix: str) -> dict:
    suffix = uuid4().hex[:8]
    return {
        "first_name": "Test",
        "last_name": "User",
        "email": f"{prefix}{suffix}@example.com",
        "username": f"{prefix}{suffix}",
        "password": "SecurePass123!",
        "confirm_password": "SecurePass123!",
    }

def test_register_duplicate_user_rejected(base_url: str):
    user = _new_user("dup")
    assert requests.post(f"{base_url}/users/register", json=user).status_code == 201
    response = requests.post(f"{base_url}/users/register", json=user)
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]

def test_register_password_mismatch_rejected(base_url: str):
    user = _new_user("mismatch")
    user["confirm_password"] = "DifferentPass123!"
    response = requests.post(f"{base_url}/users/register", json=user)
    assert response.status_code == 422

def test_register_does_not_return_password(base_url: str):
    user = _new_user("nopw")
    data = requests.post(f"{base_url}/users/register", json=user).json()
    assert "password" not in data

def test_login_wrong_password_rejected(base_url: str):
    user = _new_user("wrongpw")
    assert requests.post(f"{base_url}/users/register", json=user).status_code == 201
    response = requests.post(
        f"{base_url}/users/login",
        json={"username": user["username"], "password": "WrongPass123!"},
    )
    assert response.status_code == 401

def test_login_unknown_user_rejected(base_url: str):
    response = requests.post(
        f"{base_url}/users/login",
        json={"username": "nosuchuser", "password": "SecurePass123!"},
    )
    assert response.status_code == 401

def test_login_with_email(base_url: str):
    user = _new_user("byemail")
    assert requests.post(f"{base_url}/users/register", json=user).status_code == 201
    response = requests.post(
        f"{base_url}/users/login",
        json={"username": user["email"], "password": user["password"]},
    )
    assert response.status_code == 200
    assert response.json()["username"] == user["username"]

def test_users_me_requires_auth(base_url: str):
    assert requests.get(f"{base_url}/users/me").status_code == 401
    bad = requests.get(f"{base_url}/users/me", headers={"Authorization": "Bearer not-a-token"})
    assert bad.status_code == 401

def test_users_me_returns_current_user(base_url: str):
    user = _new_user("me")
    token = register_and_login(base_url, user)["access_token"]
    response = requests.get(f"{base_url}/users/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == user["username"]
    assert data["email"] == user["email"]

def test_users_token_form_login(base_url: str):
    user = _new_user("form")
    assert requests.post(f"{base_url}/users/register", json=user).status_code == 201
    response = requests.post(
        f"{base_url}/users/token",
        data={"username": user["username"], "password": user["password"]},
    )
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"

def test_legacy_auth_paths_still_work(base_url: str):
    user = _new_user("legacy")
    assert requests.post(f"{base_url}/auth/register", json=user).status_code == 201
    response = requests.post(
        f"{base_url}/auth/login",
        json={"username": user["username"], "password": user["password"]},
    )
    assert response.status_code == 200

# ---------------------------------------------------------------------------
# Calculations Endpoints Integration Tests
# ---------------------------------------------------------------------------
# Note: All calculation creation requests now use the /calculations endpoint (not /calculations/add)
def test_create_calculation_addition(base_url: str):
    user_data = {
        "first_name": "Calc",
        "last_name": "Adder",
        "email": f"calc.adder{uuid4()}@example.com",
        "username": f"calc_adder_{uuid4()}",
        "password": "SecurePass123!",
        "confirm_password": "SecurePass123!"
    }
    token_data = register_and_login(base_url, user_data)
    access_token = token_data["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{base_url}/calculations"
    payload = {
        "type": "addition",
        "inputs": [10.5, 3, 2],
        "user_id": "ignored"
    }
    response = requests.post(url, json=payload, headers=headers)
    assert response.status_code == 201, f"Addition calculation creation failed: {response.text}"
    data = response.json()
    assert "result" in data and data["result"] == 15.5, f"Expected result 15.5, got {data.get('result')}"

def test_create_calculation_subtraction(base_url: str):
    user_data = {
        "first_name": "Calc",
        "last_name": "Subtractor",
        "email": f"calc.sub{uuid4()}@example.com",
        "username": f"calc_sub_{uuid4()}",
        "password": "SecurePass123!",
        "confirm_password": "SecurePass123!"
    }
    token_data = register_and_login(base_url, user_data)
    access_token = token_data["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{base_url}/calculations"
    payload = {
        "type": "subtraction",
        "inputs": [10, 3, 2],
        "user_id": "ignored"
    }
    response = requests.post(url, json=payload, headers=headers)
    assert response.status_code == 201, f"Subtraction calculation creation failed: {response.text}"
    data = response.json()
    # Expected result: 10 - 3 - 2 = 5
    assert "result" in data and data["result"] == 5, f"Expected result 5, got {data.get('result')}"

def test_create_calculation_multiplication(base_url: str):
    user_data = {
        "first_name": "Calc",
        "last_name": "Multiplier",
        "email": f"calc.mult{uuid4()}@example.com",
        "username": f"calc_mult_{uuid4()}",
        "password": "SecurePass123!",
        "confirm_password": "SecurePass123!"
    }
    token_data = register_and_login(base_url, user_data)
    access_token = token_data["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{base_url}/calculations"
    payload = {
        "type": "multiplication",
        "inputs": [2, 3, 4],
        "user_id": "ignored"
    }
    response = requests.post(url, json=payload, headers=headers)
    assert response.status_code == 201, f"Multiplication calculation creation failed: {response.text}"
    data = response.json()
    # Expected result: 2 * 3 * 4 = 24
    assert "result" in data and data["result"] == 24, f"Expected result 24, got {data.get('result')}"

def test_create_calculation_division(base_url: str):
    user_data = {
        "first_name": "Calc",
        "last_name": "Divider",
        "email": f"calc.div{uuid4()}@example.com",
        "username": f"calc_div_{uuid4()}",
        "password": "SecurePass123!",
        "confirm_password": "SecurePass123!"
    }
    token_data = register_and_login(base_url, user_data)
    access_token = token_data["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{base_url}/calculations"
    payload = {
        "type": "division",
        "inputs": [100, 2, 5],
        "user_id": "ignored"
    }
    response = requests.post(url, json=payload, headers=headers)
    assert response.status_code == 201, f"Division calculation creation failed: {response.text}"
    data = response.json()
    # Expected result: 100 / 2 / 5 = 10
    assert "result" in data and data["result"] == 10, f"Expected result 10, got {data.get('result')}"

def test_list_get_update_delete_calculation(base_url: str):
    user_data = {
        "first_name": "Calc",
        "last_name": "CRUD",
        "email": f"calc.crud{uuid4()}@example.com",
        "username": f"calc_crud_{uuid4()}",
        "password": "SecurePass123!",
        "confirm_password": "SecurePass123!"
    }
    token_data = register_and_login(base_url, user_data)
    access_token = token_data["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    
    # Create a calculation (e.g., multiplication)
    create_url = f"{base_url}/calculations"
    payload = {
        "type": "multiplication",
        "inputs": [3, 4],
        "user_id": "ignored"
    }
    create_response = requests.post(create_url, json=payload, headers=headers)
    assert create_response.status_code == 201, f"Calculation creation failed: {create_response.text}"
    calc = create_response.json()
    calc_id = calc["id"]
    
    # List calculations
    list_url = f"{base_url}/calculations"
    list_response = requests.get(list_url, headers=headers)
    assert list_response.status_code == 200, f"List calculations failed: {list_response.text}"
    calc_list = list_response.json()
    assert any(c["id"] == calc_id for c in calc_list), "Created calculation not found in list"
    
    # Get calculation by ID
    get_url = f"{base_url}/calculations/{calc_id}"
    get_response = requests.get(get_url, headers=headers)
    assert get_response.status_code == 200, f"Get calculation failed: {get_response.text}"
    get_calc = get_response.json()
    assert get_calc["id"] == calc_id, "Mismatch in calculation id"
    
    # Update calculation: change inputs (e.g., from [3,4] to [5,6])
    update_url = f"{base_url}/calculations/{calc_id}"
    update_payload = {"type": "multiplication", "inputs": [5, 6]}
    update_response = requests.put(update_url, json=update_payload, headers=headers)
    assert update_response.status_code == 200, f"Update calculation failed: {update_response.text}"
    updated_calc = update_response.json()
    # For multiplication, expected result = 5 * 6 = 30
    expected_result = 30
    assert updated_calc["result"] == expected_result, f"Expected updated result {expected_result}, got {updated_calc['result']}"
    
    # Delete calculation
    delete_url = f"{base_url}/calculations/{calc_id}"
    delete_response = requests.delete(delete_url, headers=headers)
    assert delete_response.status_code == 204, f"Delete calculation failed: {delete_response.text}"
    
    # Verify deletion: GET should return 404
    get_response_after_delete = requests.get(get_url, headers=headers)
    assert get_response_after_delete.status_code == 404, "Expected 404 after deletion"

def _auth_headers(base_url: str, prefix: str) -> dict:
    token = register_and_login(base_url, _new_user(prefix))["access_token"]
    return {"Authorization": f"Bearer {token}"}

def _create_calc(base_url: str, headers: dict, calc_type: str, inputs: list) -> dict:
    response = requests.post(
        f"{base_url}/calculations", json={"type": calc_type, "inputs": inputs}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()

def test_calculations_require_auth(base_url: str):
    fake_id = str(uuid4())
    assert requests.get(f"{base_url}/calculations").status_code == 401
    assert requests.post(f"{base_url}/calculations", json={"type": "addition", "inputs": [1, 2]}).status_code == 401
    assert requests.get(f"{base_url}/calculations/{fake_id}").status_code == 401
    assert requests.put(f"{base_url}/calculations/{fake_id}", json={"type": "addition", "inputs": [1, 2]}).status_code == 401
    assert requests.patch(f"{base_url}/calculations/{fake_id}", json={"inputs": [1, 2]}).status_code == 401
    assert requests.delete(f"{base_url}/calculations/{fake_id}").status_code == 401

def test_create_calculation_invalid_payloads(base_url: str):
    headers = _auth_headers(base_url, "calcbad")
    url = f"{base_url}/calculations"
    assert requests.post(url, json={"type": "modulus", "inputs": [1, 2]}, headers=headers).status_code == 422
    assert requests.post(url, json={"type": "addition", "inputs": [1]}, headers=headers).status_code == 422
    assert requests.post(url, json={"type": "division", "inputs": [1, 0]}, headers=headers).status_code == 422

def test_browse_lists_only_own_calculations_newest_first(base_url: str):
    headers_a = _auth_headers(base_url, "browsea")
    headers_b = _auth_headers(base_url, "browseb")
    first = _create_calc(base_url, headers_a, "addition", [1, 1])
    second = _create_calc(base_url, headers_a, "addition", [2, 2])
    _create_calc(base_url, headers_b, "addition", [9, 9])

    calcs = requests.get(f"{base_url}/calculations", headers=headers_a).json()
    assert [c["id"] for c in calcs] == [second["id"], first["id"]]

def test_other_users_calculation_is_not_accessible(base_url: str):
    owner = _auth_headers(base_url, "owner")
    intruder = _auth_headers(base_url, "intruder")
    calc = _create_calc(base_url, owner, "addition", [1, 2])
    url = f"{base_url}/calculations/{calc['id']}"

    assert requests.get(url, headers=intruder).status_code == 404
    assert requests.put(url, json={"type": "addition", "inputs": [5, 5]}, headers=intruder).status_code == 404
    assert requests.delete(url, headers=intruder).status_code == 404
    # Still intact for the owner
    assert requests.get(url, headers=owner).json()["result"] == 3

def test_read_missing_and_invalid_ids(base_url: str):
    headers = _auth_headers(base_url, "readids")
    assert requests.get(f"{base_url}/calculations/{uuid4()}", headers=headers).status_code == 404
    assert requests.get(f"{base_url}/calculations/not-a-uuid", headers=headers).status_code == 400
    assert requests.delete(f"{base_url}/calculations/{uuid4()}", headers=headers).status_code == 404

def test_patch_updates_inputs(base_url: str):
    headers = _auth_headers(base_url, "patch")
    calc = _create_calc(base_url, headers, "subtraction", [10, 3])
    response = requests.patch(f"{base_url}/calculations/{calc['id']}", json={"inputs": [20, 5]}, headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["type"] == "subtraction"
    assert data["inputs"] == [20, 5]
    assert data["result"] == 15

def test_edit_changes_type_and_recomputes(base_url: str):
    headers = _auth_headers(base_url, "chtype")
    calc = _create_calc(base_url, headers, "addition", [6, 3])
    url = f"{base_url}/calculations/{calc['id']}"

    response = requests.patch(url, json={"type": "division"}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["type"] == "division"
    assert response.json()["result"] == 2

    # The change is persisted
    reread = requests.get(url, headers=headers).json()
    assert reread["type"] == "division"
    assert reread["result"] == 2

def test_edit_division_by_zero_rejected_and_unchanged(base_url: str):
    headers = _auth_headers(base_url, "div0")
    calc = _create_calc(base_url, headers, "division", [10, 2])
    url = f"{base_url}/calculations/{calc['id']}"

    response = requests.patch(url, json={"inputs": [10, 0]}, headers=headers)
    assert response.status_code == 400
    assert "zero" in response.json()["detail"].lower()

    unchanged = requests.get(url, headers=headers).json()
    assert unchanged["inputs"] == [10, 2]
    assert unchanged["result"] == 5

def test_edit_invalid_payloads(base_url: str):
    headers = _auth_headers(base_url, "editbad")
    calc = _create_calc(base_url, headers, "addition", [1, 2])
    url = f"{base_url}/calculations/{calc['id']}"
    assert requests.patch(url, json={"inputs": [1]}, headers=headers).status_code == 422
    assert requests.patch(url, json={"type": "modulus"}, headers=headers).status_code == 422
    # PUT is a full replacement: a partial body is rejected
    assert requests.put(url, json={"inputs": [3, 4]}, headers=headers).status_code == 422
    assert requests.put(url, json={"type": "division"}, headers=headers).status_code == 422

# ---------------------------------------------------------------------------
# Direct Model Tests for Calculation Operations
# ---------------------------------------------------------------------------
def test_model_addition():
    dummy_user_id = uuid4()
    calc = Calculation.create("addition", dummy_user_id, [1, 2, 3])
    result = calc.get_result()
    assert result == 6, f"Addition result incorrect: expected 6, got {result}"

def test_model_subtraction():
    dummy_user_id = uuid4()
    calc = Calculation.create("subtraction", dummy_user_id, [10, 3, 2])
    result = calc.get_result()
    assert result == 5, f"Subtraction result incorrect: expected 5, got {result}"

def test_model_multiplication():
    dummy_user_id = uuid4()
    calc = Calculation.create("multiplication", dummy_user_id, [2, 3, 4])
    result = calc.get_result()
    assert result == 24, f"Multiplication result incorrect: expected 24, got {result}"

def test_model_division():
    dummy_user_id = uuid4()
    calc = Calculation.create("division", dummy_user_id, [100, 2, 5])
    result = calc.get_result()
    assert result == 10, f"Division result incorrect: expected 10, got {result}"
    
    # Test division by zero error
    with pytest.raises(ValueError):
        calc_zero = Calculation.create("division", dummy_user_id, [100, 0])
        calc_zero.get_result()
