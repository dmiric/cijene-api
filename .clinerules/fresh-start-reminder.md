## Rebuild Everything Approval

When `make rebuild-everything` is suggested or required, do not execute the command automatically. Instead, clearly describe the action and its implications (e.g., "This command will stop and remove all Docker containers and volumes, then rebuild all services, leading to a fresh database state."), and explicitly ask the user for approval before proceeding.

## Fresh Start Reminder

When `make rebuild-everything` is suggested or executed, always remind the user of the full "Brand new start" sequence of commands, as outlined in the `Makefile`:

- `make rebuild-everything`
- `make crawl-all`
- `make unzip-crawler-output`
- `make import-data`
- `make enrich-data`
- `make geocode-stores`
- `make enrich CSV_FILE=./backups/users.csv TYPE=users`
- `make enrich CSV_FILE=./backups/user_locations.csv TYPE=user-locations`
- `make enrich CSV_FILE=./backups/search_keywords.csv TYPE=search-keywords`
- `make migrate-db`
