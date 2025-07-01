## Rebuild Everything Approval

## Fresh Start Workflow

When a fresh start is required (e.g., after schema changes or for a clean development environment), do not use `make rebuild-everything` directly. Instead, use `make dev-csv-start`.

-   **`make dev-csv-start`:** This command will:
    -   Stop and remove all Docker containers and volumes.
    -   Rebuild all services.
    -   Lead to a fresh database state.
    -   Import necessary data for testing.
-   **Approval:** Always clearly describe the implications of `make dev-csv-start` and explicitly ask for user approval before proceeding.
