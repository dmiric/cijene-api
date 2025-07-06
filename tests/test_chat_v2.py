import pytest
import httpx
import asyncio
import time
import subprocess
import os
import json
from uuid import UUID # Import UUID

# When running tests inside the Docker container, 'api' is the service hostname
BASE_URL = "http://api:8000/v2"
HEALTH_URL = "http://api:8000/health" # Health check endpoint

# Database connection details from .env
DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT"))
DB_USER = os.getenv("POSTGRES_USER")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD")
DB_NAME = os.getenv("POSTGRES_DB")

# Test user credentials as per instruction
TEST_USER_EMAIL = "damir.miric@gmail.com"
TEST_USER_PASSWORD = "Password123!"
TEST_USER_NAME = "Damir"

# This fixture ensures the API is running before tests
@pytest.fixture(scope="session", autouse=True)
def setup_api():
    print("\nEnsuring API is running before tests...")
    max_retries = 10
    retry_delay = 1 # seconds
    for i in range(max_retries):
        try:
            # Try hitting the health endpoint with httpx
            response = httpx.get(HEALTH_URL, timeout=1)
            if response.status_code == 200:
                print(f"API is healthy after {i+1} retries.")
                break
        except httpx.ConnectError as e:
            print(f"API not reachable via httpx, retrying in {retry_delay}s... ({i+1}/{max_retries}) - Error: {e}")
            time.sleep(retry_delay)
    else:
        pytest.fail(f"API did not become healthy after {max_retries} retries.")
    pass

@pytest.fixture(scope="function")
async def db_connection():
    """Provides an asyncpg connection for database operations."""
    import asyncpg # Import here to avoid circular dependency if used globally
    conn = await asyncpg.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME
    )
    yield conn
    await conn.close()

@pytest.fixture(scope="function")
async def authenticated_client(db_connection):
    """
    Provides an httpx client authenticated with a JWT for the specified test user.
    """
    # 1. Register the test user
    async with httpx.AsyncClient(base_url="http://api:8000") as client: # Use root base URL for auth
        register_data = {
            "name": TEST_USER_NAME,
            "email": TEST_USER_EMAIL,
            "password": TEST_USER_PASSWORD
        }
        register_response = await client.post("/auth/register", json=register_data)
        # Handle 409 Conflict if user already exists from a previous test run
        if register_response.status_code == 409:
            print(f"User {TEST_USER_EMAIL} already registered. Proceeding with login.")
        else:
            register_response.raise_for_status() # Ensure registration was successful (201)

        # 2. Manually verify email in DB for testing purposes
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

        # 3. Log in to obtain JWT
        login_data = {
            "email": TEST_USER_EMAIL,
            "password": TEST_USER_PASSWORD
        }
        login_response = await client.post("/auth/token", json=login_data)
        login_response.raise_for_status()
        access_token = login_response.json()["access_token"]

import datetime # Import datetime
from decimal import Decimal # Import Decimal

# ... (rest of the file) ...

@pytest.fixture(scope="function")
async def authenticated_client(db_connection):
    """
    Provides an httpx client authenticated with a JWT for the specified test user.
    Also ensures user locations are set up.
    """
    # 1. Register the test user
    async with httpx.AsyncClient(base_url="http://api:8000") as client: # Use root base URL for auth
        register_data = {
            "name": TEST_USER_NAME,
            "email": TEST_USER_EMAIL,
            "password": TEST_USER_PASSWORD
        }
        register_response = await client.post("/auth/register", json=register_data)
        # Handle 409 Conflict if user already exists from a previous test run
        if register_response.status_code == 409:
            print(f"User {TEST_USER_EMAIL} already registered. Proceeding with login.")
        else:
            register_response.raise_for_status() # Ensure registration was successful (201)

        # 2. Manually verify email in DB for testing purposes
        user_record = await db_connection.fetchrow(
            "SELECT id FROM users JOIN user_personal_data ON users.id = user_personal_data.user_id WHERE email = $1",
            TEST_USER_EMAIL
        )
        if user_record:
            user_id = user_record["id"] # Get the actual user_id (UUID)
            await db_connection.execute(
                "UPDATE users SET is_verified = TRUE, verification_token = NULL WHERE id = $1",
                user_id
            )
            print(f"Manually verified email for user {TEST_USER_EMAIL}.")
        else:
            pytest.fail(f"Test user {TEST_USER_EMAIL} not found in DB after registration attempt.")

        # 3. Log in to obtain JWT
        login_data = {
            "email": TEST_USER_EMAIL,
            "password": TEST_USER_PASSWORD
        }
        login_response = await client.post("/auth/token", json=login_data)
        login_response.raise_for_status()
        access_token = login_response.json()["access_token"]

        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(base_url=BASE_URL, headers=headers) as authenticated_client_instance: # Keep BASE_URL for chat routes
            # 4. Add user locations
            current_time = datetime.datetime.now(datetime.timezone.utc)

            # Location 1: Kuca
            await db_connection.execute(
                """
                INSERT INTO user_locations (user_id, address, city, zip_code, country, latitude, longitude, location, created_at, updated_at, location_name)
                VALUES ($1, $2, $3, $4, $5, $6, $7, ST_SetSRID(ST_Point($11, $12), 4326), $8, $9, $10)
                ON CONFLICT (user_id, location_name) DO UPDATE SET
                    address = EXCLUDED.address, city = EXCLUDED.city, zip_code = EXCLUDED.zip_code, country = EXCLUDED.country,
                    latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude, location = EXCLUDED.location, updated_at = EXCLUDED.updated_at;
                """,
                user_id,
                "Duga ulica 137a", "Vinkovci", "32100", "Hrvatska",
                Decimal("45.284707407419084"), Decimal("18.79962058737874"),
                current_time, current_time, "Kuca",
                float(Decimal("45.284707407419084")), float(Decimal("18.79962058737874")) # New parameters for ST_Point
            )
            print(f"Added/Updated 'Kuca' location for user {user_id}.")

            # Location 2: Posao
            await db_connection.execute(
                """
                INSERT INTO user_locations (user_id, latitude, longitude, location, created_at, updated_at, location_name)
                VALUES ($1, $2, $3, ST_SetSRID(ST_Point($7, $8), 4326), $4, $5, $6)
                ON CONFLICT (user_id, location_name) DO UPDATE SET
                    latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude, location = EXCLUDED.location, updated_at = EXCLUDED.updated_at;
                """,
                user_id,
                Decimal("45.291735"), Decimal("18.79346"),
                current_time, current_time, "Posao",
                float(Decimal("45.291735")), float(Decimal("18.79346")) # New parameters for ST_Point
            )
            print(f"Added/Updated 'Posao' location for user {user_id}.")

            yield authenticated_client_instance, user_id # Yield client and user_id (which is a UUID)

@pytest.mark.timeout(15) # Add a 20-second timeout for the test
@pytest.mark.asyncio
async def test_chat_jaja_query(authenticated_client: tuple[httpx.AsyncClient, UUID]):
    """
    Test that querying for 'limun' (lemon) results in the correct multi_search_tool call
    with 5 sub-queries as per initial_context.py.
    """
    client, user_id = authenticated_client # Unpack the client and user_id
    client, user_id = authenticated_client # Unpack the client and user_id
    message = "limun"
    session_id = None # Initialize session_id

    # The API key is handled by the authenticated_client fixture via the Authorization header.
    # user_id should be in the request body as part of ChatRequest.

    # First request to get a session_id and trigger the tool call
    response = await client.post(
        "/chat_v2",
        json={"message_text": message}, # user_id is now derived from auth header
        timeout=30.0 # Increase timeout for potentially long AI responses
    )
    response.raise_for_status()

    tool_call_content_found = None
    full_response_content = ""
    end_event_received = False

    async for chunk in response.aiter_bytes():
        decoded_chunk = chunk.decode("utf-8")
        for line in decoded_chunk.splitlines():
            if line.startswith("data:"):
                try:
                    event_data = json.loads(line[len("data:"):])
                    if event_data["type"] == "tool_call": # Expecting 'tool_call' now
                        tool_call_content_found = event_data["content"]
                    elif event_data["type"] == "text":
                        full_response_content += event_data["content"]
                    elif event_data["type"] == "end":
                        session_id = event_data.get("session_id")
                        end_event_received = True
                        break # Exit inner loop on end event
                except json.JSONDecodeError:
                    # Ignore malformed JSON lines
                    pass
        if end_event_received:
            break # Exit outer loop if end event received

    assert tool_call_content_found is not None, f"Expected 'tool_call' in response, but got: {full_response_content}"
    assert end_event_received, f"Expected 'end' event in response, but did not receive it. Full response: {full_response_content}"
    assert full_response_content, f"Expected natural language response after tool call, but got empty. Full response: {full_response_content}"
    assert session_id is not None, "Expected session_id to be present in the 'end' event."

    # The tool_call_content_found is now a dictionary, not a string.
    # We need to assert its structure.
    assert "name" in tool_call_content_found
    assert tool_call_content_found["name"] == "multi_search_tool"
    assert "args" in tool_call_content_found
    assert "queries" in tool_call_content_found["args"]
    
    parsed_queries = tool_call_content_found["args"]["queries"]

    assert len(parsed_queries) == 5, f"Expected 5 sub-queries, but found {len(parsed_queries)}"

    # As per user feedback, we only check the count of queries, not their exact content,
    # as the AI's specific query generation can vary.
    # We still ensure the 'limit' argument is a float (3.0) as observed from AI output.
    for query_item in parsed_queries:
        assert "name" in query_item and query_item["name"] == "search_products_v2"
        assert "arguments" in query_item and "limit" in query_item["arguments"]
        assert query_item["arguments"]["limit"] == 3.0 # Ensure limit is 3.0 (float)

    print(f"Successfully verified multi_search_tool call for '{message}' with 5 sub-queries and natural language response.")

    # Optional: Follow-up request to ensure no infinite loop on subsequent calls
    # This part is more for observing behavior than a strict assertion,
    # as the primary fix should prevent the loop in the first place.
    print(f"Making a follow-up request with session_id: {session_id}")
    follow_up_message = "Hvala!" # A simple thank you to continue the conversation
    follow_up_response = await client.post(
        "/chat_v2",
        json={"message_text": follow_up_message, "session_id": str(session_id)},
        timeout=30.0
    )
    follow_up_response.raise_for_status()

    follow_up_content = ""
    follow_up_end_event_received = False
    async for chunk in follow_up_response.aiter_bytes():
        decoded_chunk = chunk.decode("utf-8")
        for line in decoded_chunk.splitlines():
            if line.startswith("data:"):
                try:
                    event_data = json.loads(line[len("data:"):])
                    if event_data["type"] == "text":
                        follow_up_content += event_data["content"]
                    elif event_data["type"] == "end":
                        follow_up_end_event_received = True
                        break
                except json.JSONDecodeError:
                    pass
        if follow_up_end_event_received:
            break
    
    assert follow_up_end_event_received, "Expected 'end' event in follow-up response."
    assert follow_up_content, "Expected natural language response in follow-up."
    print(f"Successfully received natural language response for follow-up: {follow_up_content}")
