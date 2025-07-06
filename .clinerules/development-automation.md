## Brief overview
This guideline outlines preferences and automated workflows for development tasks, including database setup, data enrichment, and Git operations.

## Data Enrichment
-   **`g_products` Variants Column Import:** When enriching `g_products` data from CSV, the `variants` column (JSONB type in DB) must be explicitly converted to a JSON string using `json.dumps()` before insertion via `asyncpg.copy_records_to_table`.
    -   **Error Handling:** `service/db/enrich.py` includes `try-except json.JSONDecodeError` for `variants` parsing to log problematic strings.
    -   **Database Insertion:** `service/db/repositories/golden_product_repo.py` ensures `variants` is included in the `records` tuple and `columns` list for `add_many_g_products`.
-   **Handling Existing Records:** When importing data that might have unique constraints (e.g., `ean` in `g_products`), the enrichment process should handle updates for existing records (e.g., using `INSERT ... ON CONFLICT DO UPDATE`).

## Git Workflow
-   **Automated Push (`make gpush`):** A `make gpush` command is available to streamline Git operations.
    -   **Usage:** `make gpush M="Your commit message"`
    -   **Functionality:** This command performs `git add .`, `git commit -m "MESSAGE"`, and `git push`.
    -   **Trigger:** Use this command automatically when the user explicitly states "task complete".
    -   **Commit Message:** Provide a sensible and descriptive commit message that summarizes only the changes made in the most recent, distinct task that was completed, not the entire prompt session. For example, if the last task you completed was fixing the 'variants' column import, your commit message should be about that specific fix, not about previous tasks like pgAdmin setup or adding the 'gpush' command.
