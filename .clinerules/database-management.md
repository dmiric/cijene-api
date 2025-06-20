## Brief overview
These guidelines cover best practices and common pitfalls encountered during database management, particularly with PostgreSQL and Docker Compose, based on recent troubleshooting.

## Database Credentials and Environment Variables
-   **Consistency:** Ensure `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` are consistent across your `.env` files (local and server) and any related configuration files (e.g., `pgadmin/servers.json`).
-   **Trailing Spaces:** Always double-check for and remove any trailing spaces or invisible characters in `.env` variable values, especially for passwords. These can cause subtle authentication failures.
-   **Explicit Passing:** When using `docker compose exec` or `docker compose run` for commands that require environment variables (like `PGPASSWORD` or `DB_DSN`), prefer passing them explicitly with the `--env` flag to avoid shell quoting and interpretation issues.
    -   Example: `docker compose exec --env "PGPASSWORD=${DB_PASSWORD}" db psql ...`
    -   Example: `docker compose run --rm --env DB_DSN="..." api python ...`
-   **`docker compose run` syntax:** Ensure `--env` flags are placed *before* the service name when using `docker compose run`.

## Docker Compose Usage for Database Operations
-   **Environment Separation:** Use `docker-compose.yml` for server/production environments and `docker-compose.local.yml` for local development. Avoid using `.local.yml` on the server unless specifically intended for a hybrid setup.
-   **Full Rebuild:** For a clean slate (e.g., after changing database credentials), use `docker compose down -v` to stop containers and remove all associated volumes, followed by `docker compose up -d --build --force-recreate`.
-   **Makefile OS Detection:** For OS-specific commands in Makefiles, use the `IS_WINDOWS` variable (derived from `$(OS)`) for reliable detection, rather than relying on a user-defined `ENVIRONMENT` variable for this purpose.

## PostgreSQL Backup and Restore Best Practices
-   **Clean Restore:** When restoring a full database backup with `pg_restore --clean`, explicitly drop and recreate the target database just before the restore operation. This ensures a truly empty database and avoids conflicts with existing objects or initial schema creation scripts.
    -   Example: `psql -d postgres -c "DROP DATABASE IF EXISTS your_db WITH (FORCE);"` followed by `psql -d postgres -c "CREATE DATABASE your_db OWNER your_user;"`
-   **Ownership and Privileges:** Use `--no-owner` and `--no-privileges` flags with `pg_restore` to prevent issues related to object ownership and privilege settings from the backup file, especially when restoring to a different user or environment.
-   **Expected Warnings:** When `pg_restore --clean` is run on an empty database, it will produce warnings (e.g., "relation does not exist") as it attempts to drop non-existent objects. These are typically non-fatal and can be ignored if the restore completes successfully.

## Debugging Strategies
-   **Layered Approach:** When troubleshooting database connectivity or script failures, debug layer by layer:
    1.  Verify `.env` file content.
    2.  Inspect container environment variables (`docker compose exec <service> env`).
    3.  Test direct connections (e.g., `psql` from one container to another).
    4.  Enable shell debugging (`set -x`) in scripts to trace command execution and variable expansion.
-   **Manual Command Execution:** If a script fails silently, extract and run individual commands manually to pinpoint the exact point of failure and observe their direct output.
