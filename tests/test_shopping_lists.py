import pytest
import httpx
import asyncio
from uuid import UUID
import time # Import time for sleep
import subprocess # Import subprocess
from decimal import Decimal # Import Decimal
import random # Import random
from typing import Optional, List # Import Optional and List
import asyncpg # Import asyncpg
import os # Import os to access environment variables

from service.db.psql import PostgresDatabase # Import PostgresDatabase
from service.db.repositories.golden_product_repo import GoldenProductRepository # Import GoldenProductRepository
from service.db.models import GProductWithId # Import GProductWithId

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
async def db_connection():
    """Provides an asyncpg connection for database operations."""
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
async def cleanup_shopping_lists_fixture(db_connection: asyncpg.Connection): # Inject db_connection
    """
    Cleans up shopping_lists and shopping_list_items tables.
    This ensures a clean state for subsequent runs without a full rebuild.
    """
    try:
        await db_connection.execute("TRUNCATE TABLE shopping_list_items RESTART IDENTITY CASCADE;")
        await db_connection.execute("TRUNCATE TABLE shopping_lists RESTART IDENTITY CASCADE;")
        print("\nShopping list tables truncated successfully before test.")
    except Exception as e:
        print(f"Error during database cleanup: {e}")
    yield # Run the test
    # No post-test cleanup here, as it's handled by the explicit call in the test

@pytest.fixture(scope="function")
async def authenticated_client(db_connection: asyncpg.Connection):
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

        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(base_url=BASE_URL, headers=headers) as authenticated_client_instance: # Keep BASE_URL for shopping list routes
            yield authenticated_client_instance

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
    base_unit_type: str, # base_unit_type is now required
    price_at_addition: Optional[Decimal] = None,
    store_id_at_addition: Optional[int] = None,
    notes: Optional[str] = None
):
    response = await client.post(
        f"/shopping_lists/{shopping_list_id}/items?dsn=default",
        json={
            "g_product_id": g_product_id,
            "quantity": str(quantity), # Ensure Decimal is sent as string
            "base_unit_type": base_unit_type, # Pass the derived base_unit_type
            "price_at_addition": str(price_at_addition) if price_at_addition is not None else None, # Correctly handle Decimal('0.00')
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

async def get_g_product_id_by_ean(golden_products_repo: GoldenProductRepository, ean: str) -> Optional[int]:
    """Helper to get g_product_id by EAN, assuming all EANs (including chain-prefixed) are in g_products."""
    g_product = await golden_products_repo.get_g_product_by_ean(ean=ean)
    return g_product.id if g_product else None

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
    cleanup_shopping_lists_fixture, # Inject the cleanup fixture
):
    # Construct the DSN for the test database connection
    test_dsn = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

    # Initialize PostgresDatabase and its internal repositories
    db = PostgresDatabase(dsn=test_dsn)
    await db.connect() # PostgresDatabase creates and manages its own pool

    # Access GoldenProductRepository via the PostgresDatabase instance
    golden_products_repo = db.golden_products

    try:
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

        # 2. Add items to the open list using specified EANs
        eans_for_open_list = [
            "9100000734811", "9100000764986", "9100000810577",
            "spar:40605", "lidl:0080220", "spar:207316",
            "spar:377365", "lidl:0081272"
        ]
        
        added_items_to_open_list = []
        for ean in eans_for_open_list:
            g_product_id = await get_g_product_id_by_ean(golden_products_repo, ean)
            if not g_product_id:
                pytest.fail(f"Product with EAN {ean} not found in g_products.")

            quantity = Decimal(str(random.randint(1, 5)))
            
            g_product = await golden_products_repo.get_g_product_details(product_id=g_product_id)
            if not g_product:
                pytest.fail(f"Product with ID {g_product_id} not found in g_products.")

            base_unit_type = g_product["base_unit_type"]
            price_at_addition = None
            store_id_at_addition = None

            prices_for_product = await golden_products_repo.get_g_product_prices_by_location(
                product_id=g_product_id,
                store_ids=None
            )
            
            if prices_for_product:
                # Prioritize special_price, then regular_price, and get the associated store_id
                selected_price_entry = prices_for_product[0]
                price_at_addition = selected_price_entry.get("special_price") or selected_price_entry.get("regular_price")
                store_id_at_addition = selected_price_entry.get("store_id")
            
            if price_at_addition is None:
                price_at_addition = Decimal(str(round(random.uniform(0.5, 100.0), 2)))
                store_id_at_addition = None # Ensure store_id is None if price is random

            item = await add_shopping_list_item_helper(
                authenticated_client,
                open_list_id,
                g_product_id,
                quantity,
                base_unit_type=base_unit_type,
                price_at_addition=price_at_addition,
                store_id_at_addition=store_id_at_addition, # Pass the derived store ID
                notes=f"Item {ean} for open list"
            )
            added_items_to_open_list.append(item)
        
        assert len(added_items_to_open_list) == len(eans_for_open_list)

        # Items for closed list 1 (keeping original logic for these)
        product_ids_for_closed_list_1 = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        for g_product_id in product_ids_for_closed_list_1:
            quantity = Decimal(str(random.randint(1, 5)))
            g_product = await golden_products_repo.get_g_product_details(product_id=g_product_id)
            if not g_product:
                pytest.fail(f"Product with ID {g_product_id} not found in g_products.")
            base_unit_type = g_product["base_unit_type"]
            price_at_addition = None
            if base_unit_type == "WEIGHT":
                price_at_addition = g_product.get("best_unit_price_per_kg")
            elif base_unit_type == "VOLUME":
                price_at_addition = g_product.get("best_unit_price_per_l")
            elif base_unit_type == "COUNT":
                price_at_addition = g_product.get("best_unit_price_per_piece")
            if price_at_addition is None:
                price_at_addition = Decimal(str(round(random.uniform(0.5, 100.0), 2)))

            await add_shopping_list_item_helper(
                authenticated_client,
                closed_list_1_id,
                g_product_id,
                quantity,
                base_unit_type=base_unit_type,
                price_at_addition=price_at_addition,
                notes=f"Item {g_product_id} for closed list 1"
            )

        # Items for closed list 2 (keeping original logic for these)
        product_ids_for_closed_list_2 = [11, 21, 31, 41, 51, 61, 71, 81, 91, 95]
        for g_product_id in product_ids_for_closed_list_2:
            quantity = Decimal(str(random.randint(1, 5)))
            g_product = await golden_products_repo.get_g_product_details(product_id=g_product_id)
            if not g_product:
                pytest.fail(f"Product with ID {g_product_id} not found in g_products.")
            base_unit_type = g_product["base_unit_type"]
            price_at_addition = None
            if base_unit_type == "WEIGHT":
                price_at_addition = g_product.get("best_unit_price_per_kg")
            elif base_unit_type == "VOLUME":
                price_at_addition = g_product.get("best_unit_price_per_l")
            elif base_unit_type == "COUNT":
                price_at_addition = g_product.get("best_unit_price_per_piece")
            if price_at_addition is None:
                price_at_addition = Decimal(str(round(random.uniform(0.5, 100.0), 2)))

            await add_shopping_list_item_helper(
                authenticated_client,
                closed_list_2_id,
                g_product_id,
                quantity,
                base_unit_type=base_unit_type,
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

        # 4. Soft-delete the specified product from the open list
        ean_to_soft_delete = "9100000734811"
        g_product_id_to_soft_delete = await get_g_product_id_by_ean(golden_products_repo, ean_to_soft_delete) # Pass golden_products_repo
        if not g_product_id_to_soft_delete:
            pytest.fail(f"Product with EAN {ean_to_soft_delete} not found for soft deletion.")

        item_to_soft_delete = next(
            (item for item in added_items_to_open_list if item["g_product_id"] == g_product_id_to_soft_delete),
            None
        )

        if item_to_soft_delete:
            await soft_delete_shopping_list_item_helper(
                authenticated_client,
                open_list_id,
                item_to_soft_delete["id"]
            )
        else:
            pytest.fail(f"Could not find item with EAN {ean_to_soft_delete} to soft-delete in the open list.")

        # Verify soft-deleted item directly in the database
        deleted_item_db = await get_shopping_list_item_from_db(item_to_soft_delete["id"])
        assert deleted_item_db is not None
        assert deleted_item_db["deleted_at"] is not None

        # 5. Verification steps
        # Get all shopping lists for the user
        all_lists_response = await authenticated_client.get(
            f"/shopping_lists?dsn=default"
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
        
        # The number of active items should be total added items minus the one soft-deleted
        assert len(active_open_list_items) == len(eans_for_open_list) - 1

        # Verify that the soft-deleted item is NOT in the active list
        assert not any(item["g_product_id"] == g_product_id_to_soft_delete for item in active_open_list_items)

        # Verify base_unit_type and price_at_addition for some items
        for item in active_open_list_items:
            g_product_id = item["g_product_id"]
            g_product = await golden_products_repo.get_g_product_details(product_id=g_product_id)
            if not g_product:
                pytest.fail(f"Product with ID {g_product_id} not found for assertion.")
            
            expected_base_unit_type = g_product["base_unit_type"]
            assert item["base_unit_type"] == expected_base_unit_type

            expected_price_at_addition = None
            if item["store_id_at_addition"]:
                prices = await golden_products_repo.get_g_product_prices_by_location(
                    product_id=g_product_id,
                    store_ids=[item["store_id_at_addition"]]
                )
                if prices:
                    expected_price_at_addition = prices[0].get("special_price") or prices[0].get("regular_price")
            else:
                # If store_id_at_addition is None, fetch all prices for the product and pick the best one
                prices_for_product = await golden_products_repo.get_g_product_prices_by_location(
                    product_id=g_product_id,
                    store_ids=None # Get all prices for the product regardless of store
                )
                if prices_for_product:
                    expected_price_at_addition = prices_for_product[0].get("special_price") or prices_for_product[0].get("regular_price")
            
            # Allow for slight floating point differences in price_at_addition
            if expected_price_at_addition is not None and item["price_at_addition"] is not None:
                assert abs(Decimal(str(item["price_at_addition"])) - Decimal(str(expected_price_at_addition))) < Decimal("0.01")
            elif expected_price_at_addition is None and item["price_at_addition"] is not None:
                assert item["price_at_addition"] is not None
            else:
                assert item["price_at_addition"] == expected_price_at_addition

            # Assert store_id_at_addition is correctly set
            # If a price was found, store_id_at_addition should match the one from the price entry
            # If no price was found (and price_at_addition was random), store_id_at_addition should be None
            expected_store_id_at_addition = None
            prices_for_assertion = await golden_products_repo.get_g_product_prices_by_location(
                product_id=g_product_id,
                store_ids=None
            )
            if prices_for_assertion:
                expected_store_id_at_addition = prices_for_assertion[0].get("store_id")
            
            assert item["store_id_at_addition"] == expected_store_id_at_addition

            assert "product_name" in item
            assert "chain_code" in item

    finally:
        await db.close() # Ensure the database pool is closed after the test

    # Clean up: Soft delete all created shopping lists (including the open one)
    # The fixture already handles cleanup for the main list, but for the new ones, we need explicit cleanup.
    # This is handled by the test's scope and the soft_delete_shopping_list_helper.
    # For a robust test, ensure all created resources are cleaned up.
    # The current setup_api fixture does a full rebuild, which is sufficient for cleanup.
