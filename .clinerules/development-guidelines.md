## Brief overview
This guideline consolidates best practices and common troubleshooting steps for Python development, Docker, and data enrichment within this project, based on recent interactions.

## Docker and Data Enrichment Workflow
-   **Dockerfile.postgres**: Ensure `hunspell-hr` is included in `apt-get install` for Croatian Full-Text Search (FTS).
-   **Database Initialization**: Use `docker/db/init-croatian-fts.sh` for Croatian FTS configuration. This script should be mounted to `/docker-entrypoint-initdb.d/`.
-   **Stop-word files**: Custom stop-word files (e.g., `croatian.stop`) must be explicitly mounted into the PostgreSQL container at `/usr/share/postgresql/16/tsearch_data/`.
-   **`make enrich` `TYPE` parameter**: The `--type` argument for `service/db/enrich.py` should consistently match the CSV file's logical name (e.g., `g_products` for `g_products.csv`, `g_prices` for `g_prices.csv`, `g_product-best-offers` for `g_product_best_offers.csv`).
-   **`SERIAL PRIMARY KEY` handling**: When importing CSV data into tables with `SERIAL PRIMARY KEY` columns (e.g., `id` in `g_products`, `g_prices`), do NOT pass the `id` value from the CSV to the Python model constructor or the `asyncpg.copy_records_to_table` operation. Let the database auto-generate these IDs.
-   **`VECTOR` type handling**:
    -   Ensure `pgvector[asyncpg]` is installed (add to `requirements.txt`).
    -   Register the `vector` type codec in `service/db/psql.py` within the `_init_connection` method using `await pgvector.asyncpg.register_vector(conn)`.
    -   When passing embedding data (Python `List[float]`) to `asyncpg.copy_records_to_table`, pass the list directly; do not convert it to a string (e.g., `gp.embedding`, not `str(list(gp.embedding))`).
-   **`ENUM` type handling**: When inserting string values from CSV into PostgreSQL `ENUM` columns (e.g., `base_unit_type`), explicitly cast the value in the SQL `SELECT` statement (e.g., `column_name::enum_type_name`).
-   **Robust Decimal parsing**: When converting string values from CSV to `Decimal` (e.g., `regular_price`, `special_price`, `best_unit_price_per_kg`), implement robust parsing using `try-except decimal.InvalidOperation` blocks. Handle empty strings, `"NULL"` (case-insensitive), or other non-numeric values by setting them to `None`.
-   **Missing Python modules**: If a `ModuleNotFoundError` occurs during Docker container execution, add the missing package to `requirements.txt` and rebuild the relevant Docker images (`docker compose up -d --build`).

## General Development Practices
-   **CLI Commands**: Provide commands tailored for PowerShell when applicable, in a single line.
-   **File Listing**: Prefer `list_files` tool over `ls` command for checking file existence.
-   **Log Files**: "Logs" or "log files" refer to `all_logs.log` or `parser_logs.log` in the `logs` directory.
-   **Code Writing**: Always provide complete code snippets and use `write_to_file` for file creation/overwriting.
-   **Fresh Start**: When `make rebuild-everything` is suggested or executed, always remind the user of the full "Brand new start" sequence of commands as outlined in the `Makefile`.
