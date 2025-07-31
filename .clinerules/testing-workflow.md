## Brief overview
This guideline defines the preferred workflow for testing new implementations within this project.

## Testing workflow
-   **Rebuilding the environment:** When testing new implementations, the correct command for rebuilding everything is `make dev-fresh-start`, if you determine that database dosen't need rebuilding for the changes you made use `make rebuild-api`
-   **Running tests:** After rebuilding, run the appropriate test(s) using `make test-...` (e.g., `make test-auth`, `make test-shopping-lists`). Check the `Makefile` for available test targets.
-   **Checking logs:** If any errors occur during testing, the proper way to check the API logs is by running `make logs-api`.
