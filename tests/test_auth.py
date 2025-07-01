import pytest
import httpx
import asyncio
from uuid import UUID
import time
import subprocess
from decimal import Decimal
import os
import asyncpg
from datetime import datetime, timedelta, timezone # Import timezone
from uuid import UUID, uuid4 # Import uuid4

# When running tests inside the Docker container, 'api' is the service hostname
BASE_URL = "http://api:8000" # Base URL for auth endpoints
HEALTH_URL = "http://api:8000/health"

# Database connection details from .env
DB_HOST = "db"
DB_PORT = 5432
DB_USER = os.getenv("POSTGRES_USER")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD")
DB_NAME = os.getenv("POSTGRES_DB")

# Use the test user ID and API key from .clinerules/testing-credentials.md
TEST_API_KEY = "ec7cc315-c434-4c1f-aab7-3dba3545d113"

@pytest.fixture(scope="session", autouse=True)
def setup_api():
    print("\nEnsuring API is running before tests...")
    max_retries = 10
    retry_delay = 1
    for i in range(max_retries):
        try:
            response = httpx.get(HEALTH_URL, timeout=1)
            if response.status_code == 200:
                print(f"API is healthy after {i+1} retries.")
                break
        except httpx.ConnectError as e:
            print(f"API not reachable via httpx, retrying in {retry_delay}s... ({i+1}/{max_retries}) - Error: {e}")
            try:
                curl_result = subprocess.run(
                    ["curl", "-v", HEALTH_URL],
                    capture_output=True, text=True, check=False, timeout=2
                )
                print("Curl stdout:", curl_result.stdout)
                print("Curl stderr:", curl_result.stderr)
            except Exception as curl_e:
                print(f"Curl command failed: {curl_e}")
            time.sleep(retry_delay)
    else:
        pytest.fail(f"API did not become healthy after {max_retries} retries.")
    pass

@pytest.fixture(scope="function")
async def db_connection():
    """Provides a direct database connection for setup/teardown."""
    conn = None
    try:
        conn = await asyncpg.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME
        )
        yield conn
    finally:
        if conn:
            await conn.close()

@pytest.fixture(scope="function")
async def cleanup_users_fixture(db_connection: asyncpg.Connection):
    """
    Cleans up users, user_personal_data, refresh_tokens, and password_reset_tokens tables.
    """
    try:
        await db_connection.execute("TRUNCATE TABLE refresh_tokens RESTART IDENTITY CASCADE;")
        await db_connection.execute("TRUNCATE TABLE password_reset_tokens RESTART IDENTITY CASCADE;")
        await db_connection.execute("TRUNCATE TABLE user_personal_data RESTART IDENTITY CASCADE;")
        await db_connection.execute("TRUNCATE TABLE users RESTART IDENTITY CASCADE;")
        print("\nAuth-related tables truncated successfully before test.")
    except Exception as e:
        print(f"Error during database cleanup: {e}")
    yield # Run the test

@pytest.fixture(scope="function")
async def unauthenticated_client():
    """Provides an httpx client without authentication headers."""
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        yield client

@pytest.fixture(scope="function")
async def authenticated_client_api_key():
    """Provides an httpx client with API key authentication headers."""
    headers = {"X-API-Key": TEST_API_KEY} # Changed to X-API-Key
    async with httpx.AsyncClient(base_url=BASE_URL, headers=headers) as client:
        yield client

# Helper function to get user from DB (for verification)
async def get_user_from_db(db_conn: asyncpg.Connection, email: str):
    row = await db_conn.fetchrow(
        """
        SELECT
            u.id, u.hashed_password, u.is_verified, u.verification_token,
            upd.email
        FROM users u
        JOIN user_personal_data upd ON u.id = upd.user_id
        WHERE upd.email = $1
        """,
        email
    )
    return row

@pytest.mark.asyncio
async def test_user_registration(unauthenticated_client: httpx.AsyncClient, cleanup_users_fixture, db_connection: asyncpg.Connection):
    """Test successful user registration."""
    register_data = {
        "name": "Test User",
        "email": "test.user@example.com",
        "password": "SecurePassword123!"
    }
    response = await unauthenticated_client.post("/auth/register", json=register_data)
    assert response.status_code == 201
    assert response.json()["message"] == "User registered successfully. Please check your email for verification."

    # Verify user exists in DB and is not verified
    user_record = await get_user_from_db(db_connection, register_data["email"])
    assert user_record is not None
    assert user_record["is_verified"] is False
    assert user_record["verification_token"] is not None

@pytest.mark.asyncio
async def test_user_registration_duplicate_email(unauthenticated_client: httpx.AsyncClient, cleanup_users_fixture):
    """Test user registration with a duplicate email."""
    register_data = {
        "name": "Test User",
        "email": "duplicate.email@example.com",
        "password": "SecurePassword123!"
    }
    response = await unauthenticated_client.post("/auth/register", json=register_data)
    assert response.status_code == 201 # First registration
    
    response = await unauthenticated_client.post("/auth/register", json=register_data)
    assert response.status_code == 409
    assert response.json()["detail"] == "Email already registered"

@pytest.mark.asyncio
async def test_user_login_success(unauthenticated_client: httpx.AsyncClient, cleanup_users_fixture, db_connection: asyncpg.Connection):
    """Test successful user login after registration and verification."""
    register_data = {
        "name": "Login User",
        "email": "login.user@example.com",
        "password": "LoginPassword123!"
    }
    await unauthenticated_client.post("/auth/register", json=register_data)

    # Manually verify email in DB for testing purposes
    user_record = await get_user_from_db(db_connection, register_data["email"])
    await db_connection.execute(
        "UPDATE users SET is_verified = TRUE, verification_token = NULL WHERE id = $1",
        user_record["id"]
    )

    login_data = {
        "email": register_data["email"],
        "password": register_data["password"]
    }
    response = await unauthenticated_client.post("/auth/token", json=login_data)
    assert response.status_code == 200
    token_data = response.json()
    assert "access_token" in token_data
    assert "refresh_token" in token_data
    assert token_data["token_type"] == "bearer"

    # Verify refresh token stored in DB
    refresh_token_db = await db_connection.fetchrow("SELECT * FROM refresh_tokens WHERE token = $1", token_data["refresh_token"])
    assert refresh_token_db is not None
    assert refresh_token_db["user_id"] == user_record["id"]
    # Convert expires_at to timezone-naive UTC for comparison
    assert refresh_token_db["expires_at"].replace(tzinfo=None) > datetime.utcnow()

@pytest.mark.asyncio
async def test_user_login_unverified_email(unauthenticated_client: httpx.AsyncClient, cleanup_users_fixture):
    """Test user login with an unverified email."""
    register_data = {
        "name": "Unverified User",
        "email": "unverified.user@example.com",
        "password": "UnverifiedPassword123!"
    }
    await unauthenticated_client.post("/auth/register", json=register_data)

    login_data = {
        "email": register_data["email"],
        "password": register_data["password"]
    }
    response = await unauthenticated_client.post("/auth/token", json=login_data)
    assert response.status_code == 403
    assert response.json()["detail"] == "Email not verified. Please check your email for a verification link."

@pytest.mark.asyncio
async def test_user_login_invalid_credentials(unauthenticated_client: httpx.AsyncClient, cleanup_users_fixture):
    """Test user login with invalid credentials."""
    register_data = {
        "name": "Invalid Creds User",
        "email": "invalid.creds@example.com",
        "password": "ValidPassword123!"
    }
    await unauthenticated_client.post("/auth/register", json=register_data)

    login_data = {
        "email": register_data["email"],
        "password": "WrongPassword!"
    }
    response = await unauthenticated_client.post("/auth/token", json=login_data)
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"

    login_data = {
        "email": "nonexistent@example.com",
        "password": "AnyPassword!"
    }
    response = await unauthenticated_client.post("/auth/token", json=login_data)
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"

@pytest.mark.asyncio
async def test_token_refresh_success(unauthenticated_client: httpx.AsyncClient, cleanup_users_fixture, db_connection: asyncpg.Connection):
    """Test successful token refresh."""
    register_data = {
        "name": "Refresh User",
        "email": "refresh.user@example.com",
        "password": "RefreshPassword123!"
    }
    await unauthenticated_client.post("/auth/register", json=register_data)

    user_record = await get_user_from_db(db_connection, register_data["email"])
    await db_connection.execute(
        "UPDATE users SET is_verified = TRUE, verification_token = NULL WHERE id = $1",
        user_record["id"]
    )

    login_data = {
        "email": register_data["email"],
        "password": register_data["password"]
    }
    login_response = await unauthenticated_client.post("/auth/token", json=login_data)
    initial_refresh_token = login_response.json()["refresh_token"]

    # Wait a bit to ensure new token has different timestamp if needed
    await asyncio.sleep(0.1) 

    refresh_response = await unauthenticated_client.post(
        "/auth/refresh",
        headers={"Authorization": f"Bearer {initial_refresh_token}"}
    )
    assert refresh_response.status_code == 200
    new_token_data = refresh_response.json()
    assert "access_token" in new_token_data
    assert "refresh_token" in new_token_data
    assert new_token_data["token_type"] == "bearer"
    assert new_token_data["refresh_token"] != initial_refresh_token # New refresh token should be issued

    # Verify old refresh token is deleted and new one is stored
    old_refresh_token_db = await db_connection.fetchrow("SELECT * FROM refresh_tokens WHERE token = $1", initial_refresh_token)
    assert old_refresh_token_db is None
    new_refresh_token_db = await db_connection.fetchrow("SELECT * FROM refresh_tokens WHERE token = $1", new_token_data["refresh_token"])
    assert new_refresh_token_db is not None
    assert new_refresh_token_db["user_id"] == user_record["id"]
    assert new_refresh_token_db["expires_at"].replace(tzinfo=None) > datetime.utcnow()

@pytest.mark.asyncio
async def test_token_refresh_invalid_token(unauthenticated_client: httpx.AsyncClient):
    """Test token refresh with an invalid refresh token."""
    response = await unauthenticated_client.post(
        "/auth/refresh",
        headers={"Authorization": "Bearer invalid_token"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired refresh token"

@pytest.mark.asyncio
async def test_logout_success(unauthenticated_client: httpx.AsyncClient, cleanup_users_fixture, db_connection: asyncpg.Connection):
    """Test successful user logout."""
    register_data = {
        "name": "Logout User",
        "email": "logout.user@example.com",
        "password": "LogoutPassword123!"
    }
    await unauthenticated_client.post("/auth/register", json=register_data)

    user_record = await get_user_from_db(db_connection, register_data["email"])
    await db_connection.execute(
        "UPDATE users SET is_verified = TRUE, verification_token = NULL WHERE id = $1",
        user_record["id"]
    )

    login_data = {
        "email": register_data["email"],
        "password": register_data["password"]
    }
    login_response = await unauthenticated_client.post("/auth/token", json=login_data)
    refresh_token = login_response.json()["refresh_token"]

    logout_response = await unauthenticated_client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {refresh_token}"}
    )
    assert logout_response.status_code == 204 # No Content

    # Verify refresh token is deleted from DB
    refresh_token_db = await db_connection.fetchrow("SELECT * FROM refresh_tokens WHERE token = $1", refresh_token)
    assert refresh_token_db is None

@pytest.mark.asyncio
async def test_email_verification_success(unauthenticated_client: httpx.AsyncClient, cleanup_users_fixture, db_connection: asyncpg.Connection):
    """Test successful email verification."""
    register_data = {
        "name": "Verify User",
        "email": "verify.user@example.com",
        "password": "VerifyPassword123!"
    }
    await unauthenticated_client.post("/auth/register", json=register_data)

    user_record = await get_user_from_db(db_connection, register_data["email"])
    verification_token = user_record["verification_token"]

    verify_response = await unauthenticated_client.get(f"/auth/verify-email/{verification_token}")
    assert verify_response.status_code == 200
    assert verify_response.json()["message"] == "Email verified successfully!"

    # Verify user is now verified in DB
    updated_user_record = await get_user_from_db(db_connection, register_data["email"])
    assert updated_user_record["is_verified"] is True
    assert updated_user_record["verification_token"] is None

@pytest.mark.asyncio
async def test_email_verification_invalid_token(unauthenticated_client: httpx.AsyncClient, cleanup_users_fixture):
    """Test email verification with an invalid token."""
    invalid_token = UUID("00000000-0000-0000-0000-000000000000")
    response = await unauthenticated_client.get(f"/auth/verify-email/{invalid_token}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Invalid or expired verification token"

@pytest.mark.asyncio
async def test_forgot_password_success(unauthenticated_client: httpx.AsyncClient, cleanup_users_fixture, db_connection: asyncpg.Connection):
    """Test successful forgot password request."""
    register_data = {
        "name": "Forgot User",
        "email": "forgot.user@example.com",
        "password": "ForgotPassword123!"
    }
    await unauthenticated_client.post("/auth/register", json=register_data)

    forgot_password_data = {"email": register_data["email"]}
    response = await unauthenticated_client.post("/auth/forgot-password", json=forgot_password_data)
    assert response.status_code == 200
    assert response.json()["message"] == "If an account with that email exists, a password reset link has been sent."

    # Verify reset token is created in DB
    user_record = await get_user_from_db(db_connection, register_data["email"])
    reset_token_db = await db_connection.fetchrow("SELECT * FROM password_reset_tokens WHERE user_id = $1", user_record["id"])
    assert reset_token_db is not None
    assert reset_token_db["used"] is False
    # Convert expires_at to timezone-naive UTC for comparison
    assert reset_token_db["expires_at"].replace(tzinfo=None) > datetime.utcnow()

@pytest.mark.asyncio
async def test_reset_password_success(unauthenticated_client: httpx.AsyncClient, cleanup_users_fixture, db_connection: asyncpg.Connection):
    """Test successful password reset."""
    register_data = {
        "name": "Reset User",
        "email": "reset.user@example.com",
        "password": "OldPassword123!"
    }
    await unauthenticated_client.post("/auth/register", json=register_data)

    user_record = await get_user_from_db(db_connection, register_data["email"])
    # Manually verify email in DB for testing purposes
    await db_connection.execute(
        "UPDATE users SET is_verified = TRUE, verification_token = NULL WHERE id = $1",
        user_record["id"]
    )
    
    # Manually create a password reset token for testing
    reset_token_uuid = uuid4()
    expires_at = datetime.utcnow() + timedelta(hours=1)
    await db_connection.execute(
        "INSERT INTO password_reset_tokens (user_id, token, expires_at, used) VALUES ($1, $2, $3, FALSE)",
        user_record["id"], str(reset_token_uuid), expires_at
    )

    new_password = "NewSecurePassword456!"
    reset_password_data = {
        "token": str(reset_token_uuid),
        "new_password": new_password
    }
    response = await unauthenticated_client.post("/auth/reset-password", json=reset_password_data)
    assert response.status_code == 200
    assert response.json()["message"] == "Password reset successfully!"

    # Verify password is changed and token is marked used
    updated_user_record = await get_user_from_db(db_connection, register_data["email"])
    assert updated_user_record is not None
    assert updated_user_record["hashed_password"] != user_record["hashed_password"] # Password should be different
    
    # Verify new password works
    login_response = await unauthenticated_client.post("/auth/token", json={"email": register_data["email"], "password": new_password})
    assert login_response.status_code == 200

    reset_token_db = await db_connection.fetchrow("SELECT * FROM password_reset_tokens WHERE token = $1", str(reset_token_uuid))
    assert reset_token_db is not None
    assert reset_token_db["used"] is True

@pytest.mark.asyncio
async def test_reset_password_invalid_token(unauthenticated_client: httpx.AsyncClient, cleanup_users_fixture):
    """Test password reset with an invalid token."""
    invalid_token = "invalid-reset-token"
    reset_password_data = {
        "token": invalid_token,
        "new_password": "NewPassword123!"
    }
    response = await unauthenticated_client.post("/auth/reset-password", json=reset_password_data)
    assert response.status_code == 404
    assert response.json()["detail"] == "Invalid or expired password reset token"

@pytest.mark.asyncio
async def test_protected_endpoint_with_jwt(unauthenticated_client: httpx.AsyncClient, cleanup_users_fixture, db_connection: asyncpg.Connection):
    """Test accessing a protected endpoint with a valid JWT."""
    register_data = {
        "name": "Protected JWT User",
        "email": "protected.jwt@example.com",
        "password": "ProtectedPassword123!"
    }
    await unauthenticated_client.post("/auth/register", json=register_data)

    user_record = await get_user_from_db(db_connection, register_data["email"])
    await db_connection.execute(
        "UPDATE users SET is_verified = TRUE, verification_token = NULL WHERE id = $1",
        user_record["id"]
    )

    login_data = {
        "email": register_data["email"],
        "password": register_data["password"]
    }
    login_response = await unauthenticated_client.post("/auth/token", json=login_data)
    access_token = login_response.json()["access_token"]

    # Access a protected endpoint (e.g., /v2/users/me)
    protected_response = await unauthenticated_client.get(
        "/v2/users/me",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert protected_response.status_code == 200
    assert protected_response.json()["user_id"] == str(user_record["id"])

@pytest.mark.asyncio
async def test_protected_endpoint_unauthenticated(unauthenticated_client: httpx.AsyncClient):
    """Test accessing a protected endpoint without any authentication."""
    response = await unauthenticated_client.get("/v2/users/me")
    assert response.status_code == 403 # Expect 403 Forbidden as per API behavior
    assert response.json()["detail"] == "Not authenticated" # This detail might change if 403 is from a different handler

@pytest.mark.asyncio
async def test_protected_endpoint_invalid_jwt(unauthenticated_client: httpx.AsyncClient):
    """Test accessing a protected endpoint with an invalid JWT."""
    response = await unauthenticated_client.get(
        "/v2/users/me",
        headers={"Authorization": "Bearer invalid.jwt.token"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid authentication credentials"
