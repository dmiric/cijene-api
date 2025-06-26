## Brief overview
This guideline defines the test suite and strategy for validating the v2 chat endpoints and the AI's capabilities, ensuring proper functionality of hybrid search, sorting, location-based queries, and conversational memory.

## Testing Strategy
-   **Execution Method:** All tests will be executed using the `test-scirpts\send-chat.ps1` command.
-   **Credentials:**
    -   `USER_ID`: Always `1` (for user "dmiric").
    -   `API_KEY`: `ec7cc315-c434-4c1f-aab7-3dba3545d113`.
-   **Context Retention:** For conversational follow-up tests, the `session_id` from the initial response must be used in subsequent `make chat` commands.
-   **Verification:** After each command, verify:
    -   **Tool Calls:** AI correctly identifies and calls the appropriate v2 tool (`search_products_v2`, `find_nearby_stores_v2`, `get_product_prices_by_location_v2`, `get_product_details_v2`, `get_user_locations`) with correct parameters.
    -   **Tool Outputs:** Simulated tool outputs (from `db_v2` methods) are as expected.
    -   **AI Response:** Final natural language response from the AI is accurate and relevant.

## Test Suite: Questions to Validate the AI Search System

### Category 1: Basic Search & Intelligence

-   **Simple Keyword Search:**
    -   **Question:** "Pronađi Tabasco umak"
    -   **Expected:** AI calls `search_products_v2` with `q="Tabasco umak"`. Returns multiple Tabasco products.

-   **Semantic / Vector Search:**
    -   **Question:** "Treba mi nešto ljuto za začiniti jelo"
    -   **Expected:** AI calls `search_products_v2` with `q="nešto ljuto za začiniti jelo"`. Prioritizes Tabasco products (Habanero, Chipotle).

-   **Brand-Specific Search:**
    -   **Question:** "Pokaži mi sve od OGX-a"
    -   **Expected:** AI calls `search_products_v2` with `q="OGX"` and `brand="OGX"`. Returns all OGX shampoos and conditioners.

-   **Category-Specific Search:**
    -   **Question:** "Što imaš od regeneratora za kosu?"
    -   **Expected:** AI calls `search_products_v2` with `q="regenerator za kosu"` and `category="regenerator za kosu"` (or similar inferred category). Returns only OGX conditioners.

-   **Typo Tolerance:**
    -   **Question:** "Treba mi sampon sa arganovim uljm"
    -   **Expected:** AI calls `search_products_v2` with `q="sampon sa arganovim uljm"`. Still finds OGX shampoo with argan oil.

### Category 2: Value-Based & Sorting Questions

-   **Best Value (Weight):**
    -   **Question:** "Koji Ahmad čaj mi daje najviše grama za moj novac?"
    -   **Expected:** AI calls `search_products_v2` with `q="Ahmad čaj"` and `sort_by="best_value_kg"`. Returns Ahmad teas ordered by lowest `best_unit_price_per_kg`.

-   **Best Value (Volume):**
    -   **Question:** "Koji je najpovoljniji ljuti umak po litri?"
    -   **Expected:** AI calls `search_products_v2` with `q="ljuti umak"` and `sort_by="best_value_l"`. Returns hot sauces sorted by lowest `best_unit_price_per_l`.

-   **Best Value (Count):**
    -   **Question:** "Koje salvete su najjeftinije po komadu?"
    -   **Expected:** AI calls `search_products_v2` with `q="salvete"` and `sort_by="best_value_piece"`. Returns napkins sorted by lowest `best_unit_price_per_piece`.

### Category 3: Location-Based Scenarios

-   **Simple Location-Based Search (Implicit Location):**
    -   **Question:** "Gdje mogu kupiti čaj od kamilice u mojoj blizini?"
    -   **Expected Flow:** `get_user_locations` -> `find_nearby_stores_v2` (using user's "Kuca" coordinates) -> `search_products_v2` -> `get_product_prices_by_location_v2`. Final AI response provides price and store information.

-   **Specific Location-Based Search:**
    -   **Question:** "Koja je cijena za OGX šampon blizu mog posla?"
    -   **Expected Flow:** `get_user_locations` (identifies "Posao" location) -> `find_nearby_stores_v2` (using "Posao" coordinates: 45.2917350, 18.7934600) -> `search_products_v2` -> `get_product_prices_by_location_v2`. Final AI response provides price and store information near "Posao".

-   **Best Price at a Specific Location:**
    -   **Question:** "Koji je najjeftiniji ljuti umak u Vinkovcima?"
    -   **Expected Flow:** `find_nearby_stores_v2` (for "Vinkovci") -> `search_products_v2` -> Orchestrates `get_product_prices_by_location_v2` calls for relevant products across Vinkovci stores. Final AI response highlights the cheapest hot sauce and its location.

### Category 4: Conversational Follow-up & Memory

-   **Follow-up Question:**
    -   **Initial Question:** "Pronađi mi Ahmad čaj od mente."
    -   **Follow-up Question:** "A koja mu je cijena blizu kuće?"
    -   **Expected:** AI *does not* re-run product search. Uses previous product ID, then `get_user_locations` -> `find_nearby_stores_v2` (for "Kuca") -> `get_product_prices_by_location_v2`.

## How to use `send-chat.ps1`

The `test-scripts/send-chat.ps1` script can be used to send chat messages to the v2 chat endpoint. It clears the `logs/chat-output.log` file before each run and logs the full output of the `httpie` command to this file.

### Basic Usage

You can explicitly provide the `UserId` and `ApiKey` if they differ from the defaults:

```powershell
.\test-scripts\send-chat.ps1 -Message "Pronađi Tabasco umak" -UserId "1" -ApiKey "ec7cc315-c434-4c1f-aab7-3dba3545d113"
```

### Continuing a Conversation (using Session ID)

For conversational follow-ups, you can extract the `session_id` from a previous response (found in `logs/chat-output.log` at the end of the `data: {"type": "end", "session_id": "..."}` line) and pass it to the script:

```powershell
.\test-scripts\send-chat.ps1 -Message "A koja mu je cijena blizu kuće?" -SessionId "your-extracted-session-id"
```

Remember to replace `"your-extracted-session-id"` with the actual session ID from the log.
