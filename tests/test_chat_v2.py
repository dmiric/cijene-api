import pytest
import httpx
import time
import os
import json
from uuid import UUID

# --- All your existing fixtures and constants are fine ---
# When running tests inside the Docker container, 'api' is the service hostname
BASE_URL = "http://api:8000/v2"
HEALTH_URL = "http://api:8000/health"

DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_USER = os.getenv("POSTGRES_USER")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD")
DB_NAME = os.getenv("POSTGRES_DB")

TEST_USER_EMAIL = "damir.miric@gmail.com"
TEST_USER_PASSWORD = "Password123!"
TEST_USER_NAME = "Damir"

@pytest.fixture(scope="session", autouse=True)
def setup_api():
    print("\nEnsuring API is running before tests...")
    max_retries = 15
    retry_delay = 2
    for i in range(max_retries):
        try:
            response = httpx.get(HEALTH_URL, timeout=2)
            if response.status_code == 200:
                print(f"API is healthy after {i+1} retries.")
                return
        except httpx.RequestError as e:
            print(f"API not reachable, retrying in {retry_delay}s... ({i+1}/{max_retries}) - Error: {e}")
            time.sleep(retry_delay)
    pytest.fail(f"API did not become healthy after {max_retries * retry_delay} seconds.")

@pytest.fixture(scope="function")
async def db_connection():
    import asyncpg
    conn = None
    try:
        conn = await asyncpg.connect(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME)
        yield conn
    finally:
        if conn:
            await conn.close()

# In tests/test_chat_v2.py

# In tests/test_chat_v2.py

@pytest.fixture(scope="function")
async def authenticated_client(db_connection):
    """
    Provides an httpx client authenticated with a JWT for the specified test user.
    It ensures the user exists, is verified, and then logs in to get a token.
    """
    # Use a client that targets the root of the API for auth endpoints
    async with httpx.AsyncClient(base_url="http://api:8000") as client:
        
        # 1. Register the user. It's safe to run this every time.
        # If the user already exists, the API will return a 409 Conflict, which we handle.
        register_data = {
            "name": TEST_USER_NAME, 
            "email": TEST_USER_EMAIL, 
            "password": TEST_USER_PASSWORD
        }
        register_response = await client.post("/auth/register", json=register_data)
        
        # We expect either 201 (Created) or 409 (Conflict). Any other status is a failure.
        if register_response.status_code not in [201, 409]:
            pytest.fail(
                f"User registration request failed with status {register_response.status_code}: "
                f"{register_response.text}"
            )

        # 2. Get the user's ID from the database and manually verify their email for the test.
        user_record = await db_connection.fetchrow(
            "SELECT id FROM users JOIN user_personal_data ON users.id = user_personal_data.user_id WHERE email = $1",
            TEST_USER_EMAIL
        )
        if not user_record:
            pytest.fail(f"Test user '{TEST_USER_EMAIL}' not found in DB after registration attempt.")
        
        user_id = user_record["id"]
        await db_connection.execute("UPDATE users SET is_verified = TRUE WHERE id = $1", user_id)
        
        # 3. Log in to get the JWT.
        # The /token endpoint's Pydantic model expects a JSON body with an "email" field.
        login_data = {
            "email": TEST_USER_EMAIL, 
            "password": TEST_USER_PASSWORD
        }
        
        # Use json= to send the data as 'application/json'
        login_response = await client.post("/auth/token", json=login_data)

        if login_response.status_code != 200:
            pytest.fail(
                f"Login request failed with status {login_response.status_code}: "
                f"{login_response.text}"
            )
        
        # 4. Create and yield the final, authenticated client.
        access_token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}
        
        # This client is pre-configured with the correct base URL and auth header for all subsequent API calls.
        async with httpx.AsyncClient(base_url=BASE_URL, headers=headers) as authenticated_client_instance:
            yield authenticated_client_instance

@pytest.mark.timeout(30)
@pytest.mark.asyncio
async def test_single_chat_query(authenticated_client: httpx.AsyncClient, initial_query: str | None):
    """
    This test executes a single chat query and verifies the correct type of response is received.
    - For a product query (like 'limun'), it expects a 'tool_output' event.
    - For a general question, it expects a 'text' event.

    The query can be specified via the '--query' command-line argument.
    If no query is provided, it defaults to 'limun'.
    """
    # Use the provided query, or default to "limun" if the flag isn't used
    query_to_test = initial_query or "limun"

    print(f"\n--- TESTING QUERY: '{query_to_test}' ---")

    payload = {"message_text": query_to_test}
    full_text_response = ""
    any_content_received = False
    tool_output_received = False # Flag to track if we got a tool result

    try:
        async with authenticated_client.stream("POST", "/chat_v2", json=payload, timeout=40.0) as response:
            # raise_for_status() will automatically fail the test if the status is not 2xx
            response.raise_for_status()

            print("  [INFO] Stream connected. Receiving events...")
            async for chunk in response.aiter_bytes():
                decoded_chunk = chunk.decode("utf-8")
                for line in decoded_chunk.splitlines():
                    if line.startswith("data:"):
                        try:
                            event_data = json.loads(line[len("data:"):])
                            event_type = event_data.get("type")
                            content = event_data.get("content")

                            if event_type != "end":
                                any_content_received = True
                                print(f"  [EVENT type='{event_type}'] Content: {json.dumps(content, ensure_ascii=False)}")

                            # Track the specific types of content we receive
                            if event_type == "text":
                                full_text_response += content
                            elif event_type == "tool_output":
                                tool_output_received = True

                        except json.JSONDecodeError:
                            print(f"  [WARNING] Could not decode line: {line}")

    except httpx.ReadTimeout:
        pytest.fail("The request timed out while waiting for a response.")
    except httpx.HTTPStatusError as e:
        pytest.fail(f"Request failed with status {e.response.status_code}. Body: {e.response.text}")

    # --- NEW, SMARTER ASSERTION LOGIC ---
    
    # First, a basic check that we received *something*. This catches total failures.
    assert any_content_received, f"Expected some content for '{query_to_test}', but the stream was empty or malformed."
    
    # Now, check for the correct outcome based on the type of query.
    # This is a simple heuristic for the test's purpose.
    is_product_query = "limun" in query_to_test.lower()

    if is_product_query:
        # For a product search, we expect a tool output and NO text summary.
        assert tool_output_received, "For a product query, a 'tool_output' event was expected but not found."
        assert not full_text_response, f"A text summary was not expected for a product query, but received: '{full_text_response}'"
        print("\n  [SUCCESS] Correctly received tool_output for a product query.")
    else:
        # For a general question, we expect a text response and NO tool output.
        assert full_text_response, f"Expected a final text response for '{query_to_test}', but got none."
        assert not tool_output_received, "A 'tool_output' event was not expected for a general question, but one was received."
        print(f"\n  [SUCCESS] Correctly received text response: \"{full_text_response}\"")
    
    print(f"--- Test completed successfully for query: '{query_to_test}' ---")

@pytest.mark.timeout(60)
@pytest.mark.asyncio
async def test_chat_history_modes(authenticated_client: httpx.AsyncClient, db_connection):
    """
    Tests the chat history retrieval based on session_id and ignore_session_history flag.
    """
    user_id_from_db = await db_connection.fetchval("SELECT user_id FROM user_personal_data WHERE email = $1", TEST_USER_EMAIL)
    
    print("\n--- TESTING CHAT HISTORY MODES ---")

    # 1. Populate history with messages across different "sessions"
    # Message 1 (Session 1)
    payload_1_1 = {"message_text": "My favorite fruit is apple."}
    session_id_1 = None
    async with authenticated_client.stream("POST", "/chat_v2", json=payload_1_1, timeout=40.0) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            decoded_chunk = chunk.decode("utf-8")
            for line in decoded_chunk.splitlines():
                if line.startswith("data:"):
                    event_data = json.loads(line[len("data:"):])
                    if event_data.get("type") == "end":
                        session_id_1 = UUID(event_data["content"]["session_id"])
                        print(f"  [INFO] Session 1 ID: {session_id_1}")
    assert session_id_1 is not None, "Session ID 1 not received."

    # Message 2 (Session 1)
    payload_1_2 = {"session_id": str(session_id_1), "message_text": "I also like bananas."}
    async with authenticated_client.stream("POST", "/chat_v2", json=payload_1_2, timeout=40.0) as response:
        response.raise_for_status()
        # Consume stream to ensure message is processed
        async for _ in response.aiter_bytes(): pass

    # Message 3 (Session 2 - new session)
    payload_2_1 = {"message_text": "My favorite color is blue."}
    session_id_2 = None
    async with authenticated_client.stream("POST", "/chat_v2", json=payload_2_1, timeout=40.0) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            decoded_chunk = chunk.decode("utf-8")
            for line in decoded_chunk.splitlines():
                if line.startswith("data:"):
                    event_data = json.loads(line[len("data:"):])
                    if event_data.get("type") == "end":
                        session_id_2 = UUID(event_data["content"]["session_id"])
                        print(f"  [INFO] Session 2 ID: {session_id_2}")
    assert session_id_2 is not None, "Session ID 2 not received."

    # Message 4 (Session 2)
    payload_2_2 = {"session_id": str(session_id_2), "message_text": "I prefer sunny weather."}
    async with authenticated_client.stream("POST", "/chat_v2", json=payload_2_2, timeout=40.0) as response:
        response.raise_for_status()
        # Consume stream to ensure message is processed
        async for _ in response.aiter_bytes(): pass

    # 2. Test ignore_session_history=True (default behavior)
    print("\n  [TEST] ignore_session_history=True (default)")
    payload_ignore_session = {"message_text": "What is my favorite fruit?"} # ignore_session_history defaults to True
    full_text_response_ignore = ""
    async with authenticated_client.stream("POST", "/chat_v2", json=payload_ignore_session, timeout=40.0) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            decoded_chunk = chunk.decode("utf-8")
            for line in decoded_chunk.splitlines():
                if line.startswith("data:"):
                    event_data = json.loads(line[len("data:"):])
                    if event_data.get("type") == "text":
                        full_text_response_ignore += event_data["content"]
    
    # Assert that the response contains "jabuka" (Croatian for apple) and "banane" (Croatian for bananas)
    assert "jabuka" in full_text_response_ignore.lower() and "banane" in full_text_response_ignore.lower(), \
        f"Expected 'jabuka' and 'banane' in response for ignore_session_history=True, got: {full_text_response_ignore}"
    print(f"  [SUCCESS] ignore_session_history=True: Response contains 'jabuka' and 'banane'. Full response: {full_text_response_ignore}")

    # 3. Test ignore_session_history=False (session-based)
    print("\n  [TEST] ignore_session_history=False (session-based)")

    # Test with session_id_1: Should know about fruit, not color
    payload_session_1 = {"session_id": str(session_id_1), "message_text": "What is my favorite color?", "ignore_session_history": False}
    full_text_response_session_1 = ""
    async with authenticated_client.stream("POST", "/chat_v2", json=payload_session_1, timeout=40.0) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            decoded_chunk = chunk.decode("utf-8")
            for line in decoded_chunk.splitlines():
                if line.startswith("data:"):
                    event_data = json.loads(line[len("data:"):])
                    if event_data.get("type") == "text":
                        full_text_response_session_1 += event_data["content"]
    
    # The key is that it *should not* mention "plava" (Croatian for blue) if it's strictly session-based on session_id_1.
    assert "plava" not in full_text_response_session_1.lower(), \
        f"Expected 'plava' NOT in response for session_id_1, got: {full_text_response_session_1}"
    print(f"  [SUCCESS] session_id_1 (ignore_session_history=False): Response does NOT contain 'plava'. Full response: {full_text_response_session_1}")

    # Test with session_id_2: Should know about color
    payload_session_2 = {"session_id": str(session_id_2), "message_text": "What is my favorite color?", "ignore_session_history": False}
    full_text_response_session_2 = ""
    async with authenticated_client.stream("POST", "/chat_v2", json=payload_session_2, timeout=40.0) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            decoded_chunk = chunk.decode("utf-8")
            for line in decoded_chunk.splitlines():
                if line.startswith("data:"):
                    event_data = json.loads(line[len("data:"):])
                    if event_data.get("type") == "text":
                        full_text_response_session_2 += event_data["content"]
    
    assert "plava" in full_text_response_session_2.lower(), \
        f"Expected 'plava' in response for session_id_2, got: {full_text_response_session_2}"
    print(f"  [SUCCESS] session_id_2 (ignore_session_history=False): Response contains 'plava'. Full response: {full_text_response_session_2}")

    print("\n--- All chat history mode tests completed successfully ---")
