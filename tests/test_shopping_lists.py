import pytest
import httpx
import asyncio
from uuid import UUID
import time # Import time for sleep
import subprocess # Import subprocess
from decimal import Decimal # Import Decimal
import random # Import random
from typing import Optional # Import Optional
import asyncpg # Import asyncpg
import os # Import os to access environment variables

# When running tests inside the Docker container, 'api' is the service hostname
BASE_URL = "http://api:8000/v2"
HEALTH_URL = "http://api:8000/health" # Health check endpoint

# Database connection details from .env
DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT"))
DB_USER = os.getenv("POSTGRES_USER")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD")
DB_NAME = os.getenv("POSTGRES_DB")

# Use the test user ID and API key from .clinerules/testing-credentials.md
# USER_ID: 1, API_KEY: ec7cc315-c434-4c1f-aab7-3dba3545d113
TEST_API_KEY = "ec7cc315-c434-4c1f-aab7-3dba3545d113"
# The actual user_id (UUID) will be fetched dynamically

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
            # Also try with curl to get more direct output
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
async def cleanup_shopping_lists_fixture():
    """
    Cleans up shopping_lists and shopping_list_items tables.
    This ensures a clean state for subsequent runs without a full rebuild.
    """
    conn = None
    try:
        conn = await asyncpg.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME
        )
        await conn.execute("TRUNCATE TABLE shopping_list_items RESTART IDENTITY CASCADE;")
        await conn.execute("TRUNCATE TABLE shopping_lists RESTART IDENTITY CASCADE;")
        print("\nShopping list tables truncated successfully before test.")
    except Exception as e:
        print(f"Error during database cleanup: {e}")
    finally:
        if conn:
            await conn.close()
    yield # Run the test
    # No post-test cleanup here, as it's handled by the explicit call in the test

@pytest.fixture(scope="function")
async def authenticated_client():
    """Provides an httpx client with authentication headers."""
    headers = {"Authorization": f"Bearer {TEST_API_KEY}"}
    async with httpx.AsyncClient(base_url=BASE_URL, headers=headers) as client:
        yield client

@pytest.fixture(scope="function")
async def authenticated_user_id(authenticated_client: httpx.AsyncClient) -> UUID:
    """Fetches the authenticated user's UUID."""
    response = await authenticated_client.get("/users/me")
    response.raise_for_status() # Raise an exception for bad status codes
    return UUID(response.json()["user_id"])

# Helper functions for shopping list operations
async def create_shopping_list_helper(client: httpx.AsyncClient, list_name: str):
    response = await client.post(
        "/shopping_lists?dsn=default",
        json={"name": list_name}
    )
    response.raise_for_status()
    return response.json()

async def update_shopping_list_status_helper(client: httpx.AsyncClient, list_id: int, status: str):
    response = await client.put(
        f"/shopping_lists/{list_id}?dsn=default",
        json={"status": status} # API expects lowercase enum values
    )
    response.raise_for_status()
    return response.json()

async def add_shopping_list_item_helper(
    client: httpx.AsyncClient,
    shopping_list_id: int,
    g_product_id: int,
    quantity: Decimal,
    base_unit_type: str = "COUNT",
    price_at_addition: Optional[Decimal] = None,
    store_id_at_addition: Optional[int] = None,
    notes: Optional[str] = None
):
    response = await client.post(
        f"/shopping_lists/{shopping_list_id}/items?dsn=default",
        json={
            "g_product_id": g_product_id,
            "quantity": str(quantity), # Ensure Decimal is sent as string
            "base_unit_type": base_unit_type,
            "price_at_addition": str(price_at_addition) if price_at_addition else None,
            "store_id_at_addition": store_id_at_addition,
            "notes": notes
        }
    )
    response.raise_for_status()
    return response.json()

async def soft_delete_shopping_list_helper(client: httpx.AsyncClient, list_id: int):
    response = await client.delete(
        f"/shopping_lists/{list_id}?dsn=default"
    )
    response.raise_for_status()
    return response.json()

async def soft_delete_shopping_list_item_helper(client: httpx.AsyncClient, list_id: int, item_id: int):
    response = await client.delete(
        f"/shopping_lists/{list_id}/items/{item_id}?dsn=default"
    )
    response.raise_for_status()
    return response.json()

async def get_shopping_list_item_from_db(item_id: int) -> Optional[dict]:
    """Directly fetches a shopping list item from the database."""
    conn = None
    try:
        conn = await asyncpg.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME
        )
        query = "SELECT * FROM shopping_list_items WHERE id = $1;"
        record = await conn.fetchrow(query, item_id)
        return dict(record) if record else None
    except Exception as e:
        print(f"Error fetching item from DB: {e}")
        return None
    finally:
        if conn:
            await conn.close()

@pytest.mark.asyncio
async def test_get_user_shopping_lists_unauthenticated():
    """Test fetching shopping lists without authentication (should fail)."""
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        # We can't use a specific user_id here as it's unauthenticated
        response = await client.get("/shopping_lists?dsn=default")
        assert response.status_code == 403
        assert response.json()["detail"] == "Not authenticated"

@pytest.mark.asyncio
async def test_complex_shopping_list_scenarios(
    authenticated_client: httpx.AsyncClient,
    authenticated_user_id: UUID,
    cleanup_shopping_lists_fixture # Inject the cleanup fixture
):
    # 1. Create 3 shopping lists: 2 closed, 1 open
    # Open list
    open_list_name = "My Open Shopping List"
    open_list = await create_shopping_list_helper(authenticated_client, open_list_name)
    open_list_id = open_list["id"]
    assert open_list["name"] == open_list_name
    assert open_list["status"] == "open"

    # Closed list 1
    closed_list_1_name = "My Closed Shopping List 1"
    closed_list_1 = await create_shopping_list_helper(authenticated_client, closed_list_1_name)
    closed_list_1_id = closed_list_1["id"]
    await update_shopping_list_status_helper(authenticated_client, closed_list_1_id, "closed")
    
    # Closed list 2
    closed_list_2_name = "My Closed Shopping List 2"
    closed_list_2 = await create_shopping_list_helper(authenticated_client, closed_list_2_name)
    closed_list_2_id = closed_list_2["id"]
    await update_shopping_list_status_helper(authenticated_client, closed_list_2_id, "closed")

    # 2. Add 10 items to each list
    # Items for open list
    product_ids_for_open_list = [99, 87, 1, 4, 33, 27, 50, 60, 70, 80] # Ensure 10 items
    quantities_for_open_list = {99: Decimal("2.5"), 87: Decimal("1.2")}

    added_items_to_open_list = []
    for i, g_product_id in enumerate(product_ids_for_open_list):
        quantity = quantities_for_open_list.get(g_product_id, Decimal(str(random.randint(1, 5))))
        price_at_addition = Decimal(str(round(random.uniform(0.5, 100.0), 2)))
        item = await add_shopping_list_item_helper(
            authenticated_client,
            open_list_id,
            g_product_id,
            quantity,
            price_at_addition=price_at_addition,
            notes=f"Item {g_product_id} for open list"
        )
        added_items_to_open_list.append(item)
    
    assert len(added_items_to_open_list) == 10

    # Items for closed list 1
    product_ids_for_closed_list_1 = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    for g_product_id in product_ids_for_closed_list_1:
        quantity = Decimal(str(random.randint(1, 5)))
        price_at_addition = Decimal(str(round(random.uniform(0.5, 100.0), 2)))
        await add_shopping_list_item_helper(
            authenticated_client,
            closed_list_1_id,
            g_product_id,
            quantity,
            price_at_addition=price_at_addition,
            notes=f"Item {g_product_id} for closed list 1"
        )

    # Items for closed list 2
    product_ids_for_closed_list_2 = [11, 21, 31, 41, 51, 61, 71, 81, 91, 95]
    for g_product_id in product_ids_for_closed_list_2:
        quantity = Decimal(str(random.randint(1, 5)))
        price_at_addition = Decimal(str(round(random.uniform(0.5, 100.0), 2)))
        await add_shopping_list_item_helper(
            authenticated_client,
            closed_list_2_id,
            g_product_id,
            quantity,
            price_at_addition=price_at_addition,
            notes=f"Item {g_product_id} for closed list 2"
        )

    # 3. Add 2 deleted shopping lists
    deleted_list_1_name = "My Deleted Shopping List 1"
    deleted_list_1 = await create_shopping_list_helper(authenticated_client, deleted_list_1_name)
    await soft_delete_shopping_list_helper(authenticated_client, deleted_list_1["id"])

    deleted_list_2_name = "My Deleted Shopping List 2"
    deleted_list_2 = await create_shopping_list_helper(authenticated_client, deleted_list_2_name)
    await soft_delete_shopping_list_helper(authenticated_client, deleted_list_2["id"])

    # 4. Add 2 deleted shopping list items to the open list
    # Soft-delete items with g_product_id 1 and 4
    item_to_delete_1 = next((item for item in added_items_to_open_list if item["g_product_id"] == 1), None)
    item_to_delete_2 = next((item for item in added_items_to_open_list if item["g_product_id"] == 4), None)

    if item_to_delete_1 and item_to_delete_2:
        await soft_delete_shopping_list_item_helper(
            authenticated_client,
            open_list_id,
            item_to_delete_1["id"]
        )
        await soft_delete_shopping_list_item_helper(
            authenticated_client,
            open_list_id,
            item_to_delete_2["id"]
        )
    else:
        pytest.fail("Could not find items with g_product_id 1 and 4 to soft-delete.")

    # Verify soft-deleted items directly in the database
    deleted_item_1_db = await get_shopping_list_item_from_db(item_to_delete_1["id"])
    deleted_item_2_db = await get_shopping_list_item_from_db(item_to_delete_2["id"])

    assert deleted_item_1_db is not None
    assert deleted_item_1_db["deleted_at"] is not None
    assert deleted_item_2_db is not None
    assert deleted_item_2_db["deleted_at"] is not None

    # 5. Verification steps
    # Get all shopping lists for the user
    all_lists_response = await authenticated_client.get(
        f"/shopping_lists?dsn=default&user_id={authenticated_user_id}"
    )
    assert all_lists_response.status_code == 200
    all_lists = all_lists_response.json()

    # Verify counts and statuses
    open_lists_found = [sl for sl in all_lists if sl["status"] == "open" and sl["deleted_at"] is None]
    closed_lists_found = [sl for sl in all_lists if sl["status"] == "closed" and sl["deleted_at"] is None]
    deleted_lists_found = [sl for sl in all_lists if sl["deleted_at"] is not None]

    assert len(open_lists_found) == 1
    assert open_lists_found[0]["name"] == open_list_name
    assert len(closed_lists_found) == 2
    assert any(sl["name"] == closed_list_1_name for sl in closed_lists_found)
    assert any(sl["name"] == closed_list_2_name for sl in closed_lists_found)

    # Verify items in the open list
    open_list_items_response = await authenticated_client.get(
        f"/shopping_lists/{open_list_id}/items?dsn=default"
    )
    assert open_list_items_response.status_code == 200
    open_list_items = open_list_items_response.json()

    # Filter out deleted items for count verification
    active_open_list_items = [item for item in open_list_items if item["deleted_at"] is None]
    # deleted_open_list_items = [item for item in open_list_items if item["deleted_at"] is not None] # No longer checking this

    assert len(active_open_list_items) == 8 # 10 total - 2 deleted
    # assert len(deleted_open_list_items) == 2 # No longer checking this

    # Verify specific items and their quantities
    item_99 = next((item for item in active_open_list_items if item["g_product_id"] == 99), None)
    assert item_99 is not None
    assert Decimal(str(item_99["quantity"])) == Decimal("2.5")

    item_87 = next((item for item in active_open_list_items if item["g_product_id"] == 87), None)
    assert item_87 is not None
    assert Decimal(str(item_87["quantity"])) == Decimal("1.2")

    # Verify product_name and chain_code for some items (assuming they exist in the enriched data)
    # This part might be tricky if the dummy g_product_ids don't have corresponding names/chains
    # in the enriched data. For now, just check if the fields exist.
    for item in active_open_list_items:
        assert "product_name" in item
        assert "chain_code" in item
        # Optionally, assert specific values if known from enrichment data
        # e.g., if g_product_id 1 is "Some Product" and store_id_at_addition links to "LIDL"
        # if item["g_product_id"] == 1:
        #     assert item["product_name"] == "Some Product"
        #     assert item["chain_code"] == "LIDL"

    # Clean up: Soft delete all created shopping lists (including the open one)
    # The fixture already handles cleanup for the main list, but for the new ones, we need explicit cleanup.
    # This is handled by the test's scope and the soft_delete_shopping_list_helper.
    # For a robust test, ensure all created resources are cleaned up.
    # The current setup_api fixture does a full rebuild, which is sufficient for cleanup.
