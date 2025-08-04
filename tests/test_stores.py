import pytest
import httpx
import asyncio
from uuid import UUID
import time
import subprocess
from decimal import Decimal
import os
import asyncpg

# When running tests inside the Docker container, 'api' is the service hostname
BASE_URL = "http://api:8000/v2/" # Changed to v2 endpoint with trailing slash
HEALTH_URL = "http://api:8000/health"

# Database connection details from .env
DB_HOST = "db" # Hostname of the PostgreSQL service in Docker Compose
DB_PORT = 5432 # Default PostgreSQL port
DB_USER = os.getenv("POSTGRES_USER")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD")
DB_NAME = os.getenv("POSTGRES_DB")

# Base URL for the API (without /v1) for login endpoint
API_ROOT_URL = "http://api:8000"
BASE_URL = f"{API_ROOT_URL}/v2/" # Changed to v2 endpoint with trailing slash
HEALTH_URL = f"{API_ROOT_URL}/health"

# Test user credentials
TEST_USER_EMAIL = "test.stores.user@example.com"
TEST_USER_PASSWORD = "TestPassword123!"
TEST_USER_NAME = "Test Stores User"

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
async def test_user_credentials(db_connection: asyncpg.Connection):
    """
    Registers a temporary test user, manually verifies their email in DB,
    and yields their email and password. Cleans up the user after the test.
    """
    register_data = {
        "name": TEST_USER_NAME,
        "email": TEST_USER_EMAIL,
        "password": TEST_USER_PASSWORD
    }
    print(f"\nRegistering test user: {TEST_USER_EMAIL}")
    async with httpx.AsyncClient(base_url=API_ROOT_URL) as client:
        register_response = await client.post("/auth/register", json=register_data)
        if register_response.status_code == 409:
            print(f"User {TEST_USER_EMAIL} already registered. Proceeding with manual verification and login.")
        else:
            register_response.raise_for_status() # Ensure registration was successful (201)
            print(f"User {TEST_USER_EMAIL} registered successfully.")

    # Manually verify email in DB for testing purposes
    user_record = await db_connection.fetchrow(
        "SELECT id FROM users JOIN user_personal_data ON users.id = user_personal_data.user_id WHERE email = $1",
        TEST_USER_EMAIL
    )
    if user_record:
        await db_connection.execute(
            "UPDATE users SET is_verified = TRUE, verification_token = NULL WHERE id = $1",
            user_record["id"]
        )
        print(f"Manually verified email for user {TEST_USER_EMAIL}.")
    else:
        pytest.fail(f"Test user {TEST_USER_EMAIL} not found in DB after registration attempt.")

    yield {"email": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD}

    # Clean up user from DB
    print(f"Cleaning up test user: {TEST_USER_EMAIL}")
    try:
        # First, get the user_id from user_personal_data
        user_record = await db_connection.fetchrow(
            "SELECT user_id FROM user_personal_data WHERE email = $1;",
            TEST_USER_EMAIL
        )
        if user_record:
            user_id_to_delete = user_record["user_id"]
            # Delete from the users table, which will cascade to user_personal_data
            await db_connection.execute("DELETE FROM users WHERE id = $1;", user_id_to_delete)
            print(f"Cleaned up user {TEST_USER_EMAIL} (ID: {user_id_to_delete}).")
        else:
            print(f"User {TEST_USER_EMAIL} not found for cleanup.")
    except Exception as e:
        print(f"Error cleaning up user {TEST_USER_EMAIL}: {e}")

@pytest.fixture(scope="function")
async def authenticated_client(test_user_credentials: dict):
    """Provides an httpx client with authentication headers using a JWT token."""
    # Login to get JWT token
    login_payload = {
        "email": test_user_credentials["email"],
        "password": test_user_credentials["password"]
    }
    print(f"Attempting to log in user: {test_user_credentials['email']}")
    async with httpx.AsyncClient(base_url=API_ROOT_URL) as client:
        response = await client.post("/auth/token", json=login_payload)
        if response.status_code != 200:
            pytest.fail(f"Failed to obtain JWT token for test user. Status: {response.status_code}, Response: {response.text}")
        token_data = response.json()
        access_token = token_data["access_token"]
        print(f"Successfully obtained JWT token for {test_user_credentials['email']}")

    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(base_url=BASE_URL, headers=headers, follow_redirects=True) as client:
        yield client

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
async def setup_test_store(db_connection: asyncpg.Connection):
    """
    Inserts a test chain and store with known coordinates for nearby tests,
    and cleans them up after the test.
    """
    test_chain_code = "TESTCHAIN"
    test_store_code = "TESTSTORE001"
    test_lat = Decimal("45.815399") # Example coordinates (Zagreb, Croatia)
    test_lon = Decimal("15.966568")
    
    chain_id = None
    store_id = None

    try:
        # Insert test chain
        chain_id = await db_connection.fetchval(
            "INSERT INTO chains (code) VALUES ($1) ON CONFLICT (code) DO UPDATE SET code = EXCLUDED.code RETURNING id;",
            test_chain_code
        )
        
        # Insert test store
        store_id = await db_connection.fetchval(
            """
            INSERT INTO stores (chain_id, code, type, address, city, zipcode, lat, lon, phone)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (chain_id, code) DO UPDATE SET
                type = EXCLUDED.type, address = EXCLUDED.address, city = EXCLUDED.city,
                zipcode = EXCLUDED.zipcode, lat = EXCLUDED.lat, lon = EXCLUDED.lon, phone = EXCLUDED.phone
            RETURNING id;
            """,
            chain_id,
            test_store_code,
            "supermarket",
            "Test Address 123",
            "Zagreb",
            "10000",
            test_lat,
            test_lon,
            "123-456-7890"
        )
        print(f"\nInserted test chain (ID: {chain_id}) and store (ID: {store_id}) for nearby test.")
        yield {
            "chain_id": chain_id,
            "store_id": store_id,
            "chain_code": test_chain_code,
            "store_code": test_store_code,
            "lat": test_lat,
            "lon": test_lon
        }
    finally:
        # Clean up: Delete the test store and chain
        if store_id:
            await db_connection.execute("DELETE FROM stores WHERE id = $1;", store_id)
            print(f"Cleaned up test store (ID: {store_id}).")
        if chain_id:
            await db_connection.execute("DELETE FROM chains WHERE id = $1;", chain_id)
            print(f"Cleaned up test chain (ID: {chain_id}).")


@pytest.mark.asyncio
async def test_list_nearby_stores_success(
    authenticated_client: httpx.AsyncClient,
    setup_test_store: dict
):
    """
    Test fetching nearby stores with valid coordinates and radius,
    expecting to find the inserted test store.
    """
    query_lat = setup_test_store["lat"]
    query_lon = setup_test_store["lon"]
    radius_meters = 1000 # 1 km radius, should include the store

    response = await authenticated_client.get(
        f"{BASE_URL}stores/nearby/?lat={query_lat}&lon={query_lon}&radius_meters={radius_meters}"
    )
    assert response.status_code == 200
    data = response.json()
    assert "stores" in data
    assert len(data["stores"]) >= 1 # Should find at least our test store

    found_store = next(
        (s for s in data["stores"] if s["id"] == setup_test_store["store_id"]),
        None
    )
    assert found_store is not None
    assert found_store["chain_code"] == setup_test_store["chain_code"]
    assert found_store["name"] == setup_test_store["store_code"]
    assert "distance_meters" in found_store
    assert isinstance(found_store["distance_meters"], (float, int, Decimal)) # Ensure it's a number
    assert float(found_store["distance_meters"]) <= radius_meters # Distance should be within radius

@pytest.mark.asyncio
async def test_list_nearby_stores_no_results(
    authenticated_client: httpx.AsyncClient,
    setup_test_store: dict
):
    """
    Test fetching nearby stores with coordinates far from any known store,
    expecting no results.
    """
    # Coordinates far from Zagreb (e.g., London)
    query_lat = Decimal("51.5074")
    query_lon = Decimal("0.1278")
    radius_meters = 1000 # 1 km radius

    response = await authenticated_client.get(
        f"{BASE_URL}stores/nearby/?lat={query_lat}&lon={query_lon}&radius_meters={radius_meters}"
    )
    assert response.status_code == 200
    data = response.json()
    assert "stores" in data
    assert len(data["stores"]) == 0

@pytest.mark.asyncio
async def test_list_nearby_stores_filter_by_chain(
    authenticated_client: httpx.AsyncClient,
    setup_test_store: dict
):
    """
    Test fetching nearby stores and filtering by chain code.
    """
    query_lat = setup_test_store["lat"]
    query_lon = setup_test_store["lon"]
    radius_meters = 1000
    chain_code = setup_test_store["chain_code"]

    response = await authenticated_client.get(
        f"{BASE_URL}stores/nearby/?lat={query_lat}&lon={query_lon}&radius_meters={radius_meters}&chain_code={chain_code}"
    )
    assert response.status_code == 200
    data = response.json()
    assert "stores" in data
    assert len(data["stores"]) >= 1

    # Ensure all found stores match the chain_code
    for store in data["stores"]:
        assert store["chain_code"] == chain_code
    
    found_store = next(
        (s for s in data["stores"] if s["id"] == setup_test_store["store_id"]),
        None
    )
    assert found_store is not None

@pytest.mark.asyncio
async def test_list_nearby_stores_invalid_params(
    authenticated_client: httpx.AsyncClient
):
    """
    Test fetching nearby stores with missing required parameters.
    """
    # Missing lat
    response = await authenticated_client.get(
        f"{BASE_URL}stores/nearby/?lon=15.0&radius_meters=1000"
    )
    assert response.status_code == 422 # Unprocessable Entity

    # Missing lon
    response = await authenticated_client.get(
        f"{BASE_URL}stores/nearby/?lat=45.0&radius_meters=1000"
    )
    assert response.status_code == 422

    # Missing radius_meters (should default to 5000, so expect 200 OK)
    response = await authenticated_client.get(
        f"{BASE_URL}stores/nearby/?lat=45.0&lon=15.0"
    )
    assert response.status_code == 200
