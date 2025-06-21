include .env
export ENVIRONMENT
export POSTGRES_USER
export POSTGRES_PASSWORD
export POSTGRES_DB
export SSH_USER
export SSH_IP

ifeq ($(OS),Windows_NT)
    DOCKER_BUILD_LOG_FILE := logs/docker-build.log
else
    DOCKER_BUILD_LOG_FILE := logs/docker-build.log
endif

DATE ?= $(shell date +%Y-%m-%d)
#Near by
LAT ?= 45.29278835973543
LON ?= 18.791376990006086
RADIUS ?= 1500
# Search products
STORE_IDS ?= 107,616
QUERY ?= kokos
API_KEY ?= ec7cc315-c434-4c1f-aab7-3dba3545d113
SEARCH_DATE ?= 
# Search keywords
LIMIT ?= 100
PRODUCT_NAME_FILTER ?= kokos

.PHONY: help crawl-sample rebuild rebuild-api import-data add-user search-products logs-api logs-crawler logs-tail pgtunnel ssh-server rebuild-everything logs-crawler-console unzip-crawler-output restore-tables dump-database upload-database-dump restore-database

## General Commands
help: ## Display this help message
	@grep -E '^(## .*$$|[a-zA-Z_-]+:.*?## .*$$)' $(MAKEFILE_LIST) | sort | sed -E 's/^(## .*)$$/\x1b[33m\1\x1b[0m\n/;s/^(.*?):.*?## (.*)$$/\x1b[36m\1\x1b[0m              \2/'

## Brand new start:
## make rebuild-everything
## make crawl-all
## make unzip-crawler-output
## make import-data
## make enrich-data
## make geocode-stores
## make restore-tables

## Docker & Build Commands
rebuild: ## Rebuild and restart all Docker containers
	@echo "Building and restarting Docker containers. Output redirected to $(DOCKER_BUILD_LOG_FILE)..."
ifeq ($(OS),Windows_NT)
	@powershell -Command "New-Item -ItemType Directory -Force -Path 'logs' | Out-Null"
else
	@mkdir -p logs # Ensure directory exists
endif
	docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build --force-recreate >> $(DOCKER_BUILD_LOG_FILE) 2>&1
	@echo "Docker containers rebuilt and restarted. Check $(DOCKER_BUILD_LOG_FILE) for details."
	@docker compose ps

rebuild-api: ## Rebuild and restart only the API service
	@echo "Building and restarting API service. Output redirected to $(DOCKER_BUILD_LOG_FILE)..."
ifeq ($(OS),Windows_NT)
	@powershell -Command "New-Item -ItemType Directory -Force -Path 'logs' | Out-Null"
else
	@mkdir -p logs # Ensure directory exists
endif
	docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build --force-recreate api >> $(DOCKER_BUILD_LOG_FILE) 2>&1
	@echo "API service rebuilt and restarted. Check $(DOCKER_BUILD_LOG_FILE) for details."
	@docker compose ps

rebuild-everything: ## Stop, remove all Docker containers and volumes, restart Docker, and rebuild all services with confirmation
	@if [ "$(IS_WINDOWS)" = "true" ]; then \
		pwsh -File ./scripts/rebuild.ps1; \
	else \
		bash ./scripts/rebuild.sh; \
	fi


## Crawler Commands
crawl-sample: ## Run a sample crawl for Lidl and Konzum and save console output to logs/crawler_console.log
	mkdir -p logs && docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm crawler python crawler/cli/crawl.py --chain dm,lidl > logs/crawler_console.log 2>&1
	docker cp $$(docker compose ps -q crawler):/app/output/$(DATE).zip ./output/$(DATE).zip

crawl-all: ## Crawl all data and save console output to logs/crawler_console.log
	mkdir -p logs && docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm crawler python crawler/cli/crawl.py > logs/crawler_console.log 2>&1
	docker cp $$(docker compose ps -q crawler):/app/output/$(DATE).zip ./output/$(DATE).zip

import-data: ## Import crawled data for a specific DATE (defaults to today)
	docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm crawler python service/db/import.py /app/output/$(DATE)

enrich-data: ## Enrich store and product data from enrichment CSVs
	docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm api python service/db/enrich.py --stores enrichment/stores.csv
	docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm api python service/db/enrich.py --products enrichment/products.csv


## Database Commands
dump-tables: ## Dump specified database tables to the db_backups volume and copy to local backups directory
	docker exec cijene-api-clone-backup-1 find /backups -type f -delete
	docker exec cijene-api-clone-backup-1 /scripts/backup_tables.sh
	mkdir -p backups
	docker cp cijene-api-clone-backup-1:/backups/. ./backups/

dump-database: ## Dump the entire database to a gzipped backup file in the db_backups volume and copy to local backups directory
	docker compose exec backup /scripts/dump_database.sh
	mkdir -p backups
	docker cp cijene-api-clone-backup-1:/backups/. ./backups/

restore-tables: ## Restore specified database tables from the db_backups volume. Usage: make restore-tables [TIMESTAMP=YYYYMMDD_HHMMSS]
	@echo "Copying backup files from host's ./backups/ to container's /backups/..."
	docker cp ./backups/. cijene-api-clone-backup-1:/backups/
	@echo "Starting database restore..."
	docker compose exec backup /scripts/restore_tables.sh $(TIMESTAMP)

# A helper variable to detect the OS
ifeq ($(OS),Windows_NT)
    IS_WINDOWS := true
else
    IS_WINDOWS := false
endif

restore-database: ## Restore the entire database from a gzipped backup file. Usage: make restore-database [TIMESTAMP=YYYYMMDD_HHMMSS]
	@# This target now handles OS detection directly
	@echo "Copying backup files to container..."
ifeq ($(IS_WINDOWS), true)
	# Windows commands
	pwsh -File ./scripts/copy_dump_to_container.ps1 "$(TIMESTAMP)" "$(POSTGRES_USER)" "$(POSTGRES_PASSWORD)" "$(POSTGRES_DB)" "$(DB_HOST)" "$(DB_PORT)"
	@echo "Starting database restore..."
	pwsh -File ./scripts/restore_database.ps1 "$(TIMESTAMP)" "$(POSTGRES_USER)" "$(POSTGRES_PASSWORD)" "$(POSTGRES_DB)" "$(DB_HOST)" "$(DB_PORT)"
else
	# Linux/macOS commands
	bash ./scripts/copy_dump_to_container.sh "$(TIMESTAMP)"
	@echo "Starting database restore..."
	bash ./scripts/restore_database.sh "$(TIMESTAMP)"
endif
	@echo "Database restore process completed successfully."

pgtunnel: ## Create an SSH tunnel to access PGAdmin locally on port 5060
	ssh-add ~/.ssh/github_actions_deploy_key; ssh -L 8081:localhost:80 $(SSH_USER)@$(SSH_IP)

geocode-stores: ## Geocode stores in the database that are missing latitude/longitude
	docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm api python -c "import asyncio; from service.cli.geocode_stores import geocode_stores; asyncio.run(geocode_stores())"


## Data Management Commands
unzip-crawler-output: ## Unzips the latest crawled data on the host. Usage: make unzip-crawler-output [DATE=YYYY-MM-DD]
	@if [ -z "$(DATE)" ]; then echo "Unzipping today's archive..."; else echo "Unzipping archive for $(DATE)..."; fi
	@if [ "$(ENVIRONMENT)" = "linux" ]; then \
		unzip -o './output/$(DATE).zip' -d './output/$(DATE)_unzipped'; \
	else \
		pwsh -Command "Expand-Archive -Path './output/$(DATE).zip' -DestinationPath './output/$(DATE)_unzipped' -Force"; \
	fi


## API & User Commands
add-user: ## Add a new user with a generated API key. Usage: make add-user USERNAME=your_username
	@if [ -z "$(USERNAME)" ]; then echo "Error: USERNAME is required. Usage: make add-user USERNAME=your_username"; exit 1; fi
	docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm api python service/cli/add_user.py $(USERNAME)

migrate-db: ## Apply database migrations
	docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm api python service/db/migrate.py

search-products: ## Search for products by name. Usage: make search-products QUERY="your query" API_KEY=your_api_key [STORE_IDS=val] [SEARCH_DATE=val]
	@if [ -z "$(API_KEY)" ]; then echo "Error: API_KEY is required. Usage: make search-products API_KEY=your_api_key [QUERY=your_query]"; exit 1; fi
	$(eval ENCODED_QUERY=$(shell echo "$(QUERY)" | sed 's/ /+/g'))
	$(eval DATE_PARAM=$(if $(SEARCH_DATE),&date=$(SEARCH_DATE),))
	true > search-prod.json && curl -s -H "Authorization: Bearer $(API_KEY)" "http://localhost:8000/v1/products/?q=$(ENCODED_QUERY)&store_ids=$(STORE_IDS)$(DATE_PARAM)" | jq . > prod.json

search-keywords: ## Get products to send to AI for keywording. Usage: make search-keywords API_KEY=your_api_key [LIMIT=val] [PRODUCT_NAME_FILTER=val]
	@if [ -z "$(API_KEY)" ]; then echo "Error: API_KEY is required. Usage: make search-keywords API_KEY=your_api_key"; exit 1; fi
	$(eval FILTER_PARAM=$(if $(PRODUCT_NAME_FILTER),&product_name_filter=$(PRODUCT_NAME_FILTER),))
	true > prod.json && curl -s -H "Authorization: Bearer $(API_KEY)" "http://localhost:8000/v1/search-keywords/?limit=$(LIMIT)$(FILTER_PARAM)" | jq . > prod.json

test-nearby: ## Test the nearby stores endpoint. Usage: make test-nearby [LATITUDE=val] [LONGITUDE=val] [RADIUS=val] API_KEY=your_api_key
	@if [ -z "$(API_KEY)" ]; then echo "Error: API_KEY is required. Usage: make test-nearby API_KEY=your_api_key"; exit 1; fi
	curl -s -H "Authorization: Bearer $(API_KEY)" "http://localhost:8000/v1/stores/nearby/?lat=$(LAT)&lon=$(LON)&radius_meters=$(RADIUS)" | jq .


## Logging Commands
logs-api: ## Display logs for the API service and save to ./logs/api.log (empties file first)
	mkdir -p logs && > ./logs/api.log && docker compose -f docker-compose.yml -f docker-compose.local.yml logs api > ./logs/api.log

logs-crawler: ## Display logs for the Crawler service and save to ./logs/crawler.log (empties file first)
	mkdir -p logs && > ./logs/crawler.log && docker compose -f docker-compose.yml -f docker-compose.local.yml logs crawler > ./logs/crawler.log

logs-tail: ## Continuously display logs from ./logs/api.log
	@if [ "$(ENVIRONMENT)" = "linux" ]; then \
		tail -f './logs/api.log'; \
	else \
		pwsh.exe -Command "Get-Content -Path './logs/api.log' -Wait"; \
	fi

logs-crawler-console: ## Continuously display console output from logs/crawler_console.log
	@if [ "$(ENVIRONMENT)" = "linux" ]; then \
		tail -f './logs/crawler_console.log'; \
	else \
		pwsh.exe -Command "Get-Content -Path './logs/crawler_console.log' -Wait"; \
	fi

## SSH Commands
ssh-server: ## SSH into the VPS server
	ssh-add ~/.ssh/github_actions_deploy_key; ssh $(SSH_USER)@$(SSH_IP)

upload-database-dump: ## Upload the latest full database dump to the remote server. Usage: make upload-database-dump [TIMESTAMP=YYYYMMDD_HHMMSS]
	@echo "Finding latest database dump..."
	$(eval LATEST_DUMP_FILE := $(shell ls -t backups/full_db_$(TIMESTAMP)*.sql.gz 2>/dev/null | head -n 1))
	@if [ -z "$(LATEST_DUMP_FILE)" ]; then \
		echo "Error: No full database dump found in backups/. Please run 'make dump-database' first or provide a TIMESTAMP."; \
		exit 1; \
	fi
	@echo "Uploading $(LATEST_DUMP_FILE) to $(SSH_USER)@$(SSH_IP):/home/$(SSH_USER)/pricemice/backups/"
	ssh-add ~/.ssh/github_actions_deploy_key; scp "$(LATEST_DUMP_FILE)" "$(SSH_USER)"@"$(SSH_IP)":/home/"$(SSH_USER)"/pricemice/backups/
	@echo "Database dump uploaded successfully."
