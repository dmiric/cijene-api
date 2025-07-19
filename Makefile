include .env
export ENVIRONMENT
export POSTGRES_USER
export POSTGRES_PASSWORD
export POSTGRES_DB
export SSH_USER
export SSH_IP
export PYTHONUNBUFFERED

# A helper variable to detect the OS
ifeq ($(OS),Windows_NT)
   	IS_WINDOWS := true
else
    IS_WINDOWS := false
endif

ifeq ($(OS),Windows_NT)
    DOCKER_BUILD_LOG_FILE := logs/docker-build.log
else
    DOCKER_BUILD_LOG_FILE := logs/docker-build.log
endif

DATE ?= $(shell date +%Y-%m-%d)
# Define default excluded volumes for rebuild-everything
EXCLUDE_VOLUMES ?= cijene-api-clone_crawler_data,cijene-api-clone_pgadmin_data
# Search products
API_KEY ?= ec7cc315-c434-4c1f-aab7-3dba3545d113

# Add a new variable for the query, it will be empty by default
QUERY=limun

.PHONY: help crawl rebuild rebuild-api import-data search-products logs-api logs-crawler logs-tail pgtunnel ssh-server rebuild-everything logs-crawler-console unzip-crawler-output restore-tables dump-database upload-database-dump restore-database build-worker

## General Commands
help: ## Display this help message
	@grep -E '^(## .*$$|[a-zA-Z_-]+:.*?## .*$$)' $(MAKEFILE_LIST) | sort | sed -E 's/^(## .*)$$/\x1b[33m\1\x1b[0m\n/;s/^(.*?):.*?## (.*)$$/\x1b[36m\1\x1b[0m              \2/'

## Brand new start:
## For development use make dev-fresh-start
## make rebuild-everything
## make migrate-db
## make crawl
## make import-data
## make enrich-data
## make geocode-stores
## make enrich CSV_FILE=./backups/users.csv TYPE=users
## make enrich CSV_FILE=./backups/user_locations.csv TYPE=user-locations
## make migrate-db

## Docker & Build Commands
rebuild: ## Rebuild and restart all Docker containers
	@echo "Building and restarting Docker containers. Output redirected to $(DOCKER_BUILD_LOG_FILE)..."
	@if [ "$(IS_WINDOWS)" = "true" ]; then \
		powershell -Command "New-Item -ItemType Directory -Force -Path 'logs' | Out-Null; Clear-Content $(DOCKER_BUILD_LOG_FILE)"; \
	else \
		mkdir -p logs; \
		> $(DOCKER_BUILD_LOG_FILE); \
	fi
	docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build --force-recreate >> $(DOCKER_BUILD_LOG_FILE) 2>&1
	@echo "Docker containers rebuilt and restarted. Check $(DOCKER_BUILD_LOG_FILE) for details."
	@docker compose ps

rebuild-api: ## Rebuild and restart only the API service
	@echo "Building and restarting API service. Output redirected to $(DOCKER_BUILD_LOG_FILE)..."
	@if [ "$(IS_WINDOWS)" = "true" ]; then \
		powershell -Command "New-Item -ItemType Directory -Force -Path 'logs' | Out-Null; Clear-Content $(DOCKER_BUILD_LOG_FILE)"; \
	else \
		mkdir -p logs; \
		> $(DOCKER_BUILD_LOG_FILE); \
	fi
	docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build --force-recreate api >> $(DOCKER_BUILD_LOG_FILE) 2>&1
	@echo "API service rebuilt and restarted. Check $(DOCKER_BUILD_LOG_FILE) for details."
	@docker compose ps

rebuild-everything: ## Stop, remove all Docker containers and volumes, restart Docker, and rebuild all services with confirmation. Use EXCLUDE_VOLUMES="vol1,vol2" to preserve volumes.
	@if [ "$(IS_WINDOWS)" = "true" ]; then \
		pwsh -File ./scripts/rebuild.ps1 -ExcludeVolumes "$(EXCLUDE_VOLUMES)"; \
	else \
		bash ./scripts/rebuild.sh --exclude="$(EXCLUDE_VOLUMES)"; \
	fi

build-worker: ## Stop, remove, and rebuild only the API and Crawler services without confirmation, excluding the database.
	docker compose -f docker-compose.worker.yml down --remove-orphans
	docker compose -f docker-compose.worker.yml up -d --build --force-recreate
	
dev-csv-start: ## Perform a fast fresh start for development, using sample data or existing crawled data.
	$(MAKE) rebuild-everything EXCLUDE_VOLUMES="$(EXCLUDE_VOLUMES)"

	@echo "Applying database migrations..."
	$(MAKE) migrate-db

	@echo "Enriching from backups..."
	$(MAKE) enrich CSV_FILE=./backups/chains.csv TYPE=chains
	$(MAKE) enrich CSV_FILE=./backups/users.csv TYPE=all-user-data USER_LOCATIONS_CSV_FILE=./backups/user_locations.csv
	$(MAKE) enrich CSV_FILE=./backups/g_products.csv TYPE=g_products
	$(MAKE) enrich CSV_FILE=./backups/g_prices.csv TYPE=g_prices
	$(MAKE) enrich CSV_FILE=./backups/g_product_best_offers.csv TYPE=g_product-best-offers
	$(MAKE) enrich CSV_FILE=./backups/stores.csv TYPE=stores

	@echo "Running Tests..."
	$(MAKE) test-api

	@echo "Development fresh start completed."

dev-fresh-start: ## Perform a fast fresh start for development, using sample data or existing crawled data.
	$(MAKE) rebuild-everything EXCLUDE_VOLUMES="$(EXCLUDE_VOLUMES)"

	@echo "Applying database migrations..."
	$(MAKE) migrate-db

# 	@echo "Checking for existing crawled data..."
# 	@if [ ! -f "./output/$(DATE).zip" ]; then \
# 		echo "No existing zip found. Running sample crawl for lidl, kaufland, spar..."; \
# 		$(MAKE) crawl --chain boso,eurospin,lidl,kaufland; \
# 	else \
# 		echo "Existing zip found: ./output/$(DATE).zip"; \
# 	fi

	@echo "Crawl data"
	$(MAKE) crawl CHAIN=boso,eurospin,lidl,kaufland,roto,trgovina-krk,lorenco,boso

	@echo "Importing data..."
	$(MAKE) import-data

	@echo "Enriching data..."
	$(MAKE) enrich-data

#	@echo "Normalizing data..."
#	$(MAKE) normalize-data-grok

#	@echo "Geocoding stores..."
#	$(MAKE) geocode-stores

	@echo "Enriching users and user locations from backups..."
	$(MAKE) enrich CSV_FILE=./backups/users.csv TYPE=all-user-data USER_LOCATIONS_CSV_FILE=./backups/user_locations.csv

	@echo "Development fresh start completed."

docker-prune: ## Stop all containers and perform a deep clean of the Docker system.
	@echo "Stopping all running project containers..."
	docker compose down
	@echo "Pruning Docker system. This will remove all stopped containers, all unused networks, all dangling images, and all unused build cache."
	@echo "You will be asked for confirmation."
	docker system prune -a --volumes
	@echo "Docker system prune complete."

## Crawling, importing and enriching Commands
crawl: ## Crawl data for specified chains (or all if none specified) and save console output to logs/crawler_console.log. Usage: make crawl [CHAIN=lidl,kaufland]
	@mkdir -p ./output/$(DATE)
	@if [ "$(IS_WINDOWS)" = "true" ]; then \
		mkdir -p logs && docker compose -f docker-compose.local.yml run --rm crawler python crawler/cli/crawl.py $(if $(CHAIN),--chain $(CHAIN),); \
	else \
		mkdir -p logs && docker compose -f docker-compose.worker.yml run --rm crawler python crawler/cli/crawl.py $(if $(CHAIN),--chain $(CHAIN),); \
	fi

import-data: ## Import crawled data for a specific DATE (defaults to today) and specific CHAINS. Usage: make import-data [DATE=YYYY-MM-DD] [CHAINS=chain1,chain2]
	@if [ -z "$(DATE)" ]; then DATE=$(shell date +%Y-%m-%d); fi
	@if [ -z "$(CHAINS)" ]; then \
		echo "Error: CHAINS is required for import-data. Usage: make import-data CHAINS=chain1,chain2 [DATE=YYYY-MM-DD]"; exit 1; \
	fi
	$(eval IMPORT_PATHS := $(foreach chain,$(subst ,,$(CHAINS)),/app/crawler_output/$(DATE)/$(chain).zip))
	@if [ "$(IS_WINDOWS)" = "true" ]; then \
		docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm crawler python service/cli/import.py $(IMPORT_PATHS); \
	else \
		docker compose -f docker-compose.worker.yml run --rm api python service/cli/import.py $(IMPORT_PATHS); \
	fi

## Hetzner VPS Worker Commands
run-hetzner-worker: ## Run the Hetzner VPS orchestration script in a Docker container.
	@echo "Building hetzner-worker-image..."
	docker build -t hetzner-worker-image -f vps_workers/Dockerfile.hetzner_worker .
	@echo "Running hetzner_worker.py in Docker container..."
	docker run --rm \
		--name hetzner-worker-container \
		-v "$(CURDIR)/.env:/app/.env:ro" \
		-v "$(SSH_KEY_PATH):/app/ssh_key:ro" \
		-e SSH_KEY_PATH=/app/ssh_key \
		hetzner-worker-image

normalize-golden-records: ## Orchestrate golden record creation. Usage: make normalize-golden-records NORMALIZER_TYPE=gemini|grok EMBEDDER_TYPE=gemini [NUM_WORKERS=N] [BATCH_SIZE=M]
	@if [ -z "$(NORMALIZER_TYPE)" ]; then echo "Error: NORMALIZER_TYPE is required. Usage: make normalize-golden-records NORMALIZER_TYPE=gemini|grok EMBEDDER_TYPE=gemini"; exit 1; fi
	@if [ -z "$(EMBEDDER_TYPE)" ]; then echo "Error: EMBEDDER_TYPE is required. Usage: make normalize-golden-records NORMALIZER_TYPE=gemini|grok EMBEDDER_TYPE=gemini"; exit 1; fi
	docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm api python -m service.normaliser.golden_record.orchestrator_golden_records --normalizer-type $(NORMALIZER_TYPE) --embedder-type $(EMBEDDER_TYPE) --num-workers $(NUM_WORKERS) --batch-size $(BATCH_SIZE)

calculate-prices: ## Orchestrate price calculation. Usage: make calculate-prices [NUM_WORKERS=N] [BATCH_SIZE=M]
	docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm api python -m service.normaliser.orchestrator_prices --num-workers $(NUM_WORKERS) --batch-size $(BATCH_SIZE)

update-best-offers: ## Orchestrate best offer updates. Usage: make update-best-offers [NUM_WORKERS=N] [BATCH_SIZE=M]
	docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm api python -m service.normaliser.orchestrator_best_offers --num-workers $(NUM_WORKERS) --batch-size $(BATCH_SIZE)

get-embedding: ## Process products missing embeddings from g_products table
	docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm api python service/normaliser/embedding_service.py

enrich-data: ## Enrich store and product data from enrichment CSV
	docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm api python service/cli/enrich.py --type stores ./enrichment/stores.csv
	docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm api python service/cli/enrich.py --type products ./enrichment/products.csv


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

csv-export: ## Export specified database tables to CSV files in the backups/ folder
	@mkdir -p backups
	@echo "Exporting tables to CSV..."
	@for table in chains g_products g_prices g_product_best_offers stores; do \
		echo "Exporting $$table.csv..."; \
		docker compose exec db psql -U $(POSTGRES_USER) -d $(POSTGRES_DB) -c "\COPY $$table TO '/tmp/$$table.csv' WITH (FORMAT CSV, HEADER, ENCODING 'UTF8');"; \
		docker compose cp db:/tmp/$$table.csv ./backups/$$table.csv; \
		echo "Exported $$table.csv to backups/$$table.csv"; \
	done
	@echo "All specified tables exported to CSV."

restore-tables: ## Restore specified database tables from the db_backups volume. Usage: make restore-tables [TIMESTAMP=YYYYMMDD_HHMMSS]
	@echo "Copying backup files from host's ./backups/ to container's /backups/..."
	docker cp ./backups/. cijene-api-clone-backup-1:/backups/
	@echo "Starting database restore..."
	docker compose exec backup /scripts/restore_tables.sh $(TIMESTAMP)

restore-database: ## Restore the entire database from a gzipped backup file. Usage: make restore-database [TIMESTAMP=YYYYMMDD_HHMMSS]
	@# This target now handles OS detection directly
	@echo "Copying backup files to container..."
	@if [ "$(IS_WINDOWS)" = "true" ]; then \
		pwsh -File ./scripts/copy_dump_to_container.ps1 "$(TIMESTAMP)" "$(POSTGRES_USER)" "$(POSTGRES_PASSWORD)" "$(POSTGRES_DB)" "$(DB_HOST)" "$(DB_PORT)"; \
		echo "Starting database restore..."; \
		pwsh -File ./scripts/restore_database.ps1 "$(TIMESTAMP)" "$(POSTGRES_USER)" "$(POSTGRES_PASSWORD)" "$(POSTGRES_DB)" "$(DB_HOST)" "$(DB_PORT)"; \
	else \
		bash ./scripts/copy_dump_to_container.sh "$(TIMESTAMP)"; \
		echo "Starting database restore..."; \
		bash ./scripts/restore_database.sh "$(TIMESTAMP)"; \
	fi
	@echo "Database restore process completed successfully."

pgtunnel: ## Create an SSH tunnel to access PGAdmin locally on port 5060
	ssh-add ~/.ssh/github_actions_deploy_key; ssh -L 8088:localhost:80 $(SSH_USER)@$(SSH_IP)

geocode-stores: ## Geocode stores in the database that are missing latitude/longitude
	docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm api python -c "import asyncio; from service.cli.geocode_stores import geocode_stores; asyncio.run(geocode_stores())"


## Data Management Commands
unzip-crawler-output: ## Unzips the latest crawled data on the host. Usage: make unzip-crawler-output [DATE=YYYY-MM-DD]
	@if [ -z "$(DATE)" ]; then echo "Unzipping today's archive..."; else echo "Unzipping archive for $(DATE)..."; fi
	@if [ "$(IS_WINDOWS)" = "true" ]; then \
		pwsh -Command "Expand-Archive -Path '$(CURDIR)/output/$(DATE).zip' -DestinationPath '$(CURDIR)/output/$(DATE)_unzipped' -Force"; \
	else \
		unzip -o './output/$(DATE).zip' -d './output/$(DATE)_unzipped'; \
	fi

enrich: ## Enrich data from a CSV file. Usage: make enrich CSV_FILE=./path/to/file.csv TYPE=products|stores|users|user-locations|search-keywords [USER_LOCATIONS_CSV_FILE=./path/to/user_locations.csv]
	@if [ -z "$(CSV_FILE)" ]; then echo "Error: CSV_FILE is required. Usage: make enrich CSV_FILE=./path/to/file.csv TYPE=..."; exit 1; fi
	@if [ -z "$(TYPE)" ]; then echo "Error: TYPE is required. Usage: make enrich CSV_FILE=./path/to/file.csv TYPE=..."; exit 1; fi
	$(eval USER_LOCATIONS_ARG=$(if $(USER_LOCATIONS_CSV_FILE),--user-locations-csv-file $(USER_LOCATIONS_CSV_FILE),))
	docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm api python service/cli/enrich.py --type $(TYPE) $(CSV_FILE) $(USER_LOCATIONS_ARG)


## API & User Commands
migrate-db: ## Apply database migrations
	docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm api python service/db/migrate.py

search-products: ## Search for products by name. Usage: make search-products QUERY="your query" API_KEY=your_api_key [STORE_IDS=val] [SEARCH_DATE=val]
	@if [ -z "$(API_KEY)" ]; then echo "Error: API_KEY is required. Usage: make search-products API_KEY=your_api_key [QUERY=your_query]"; exit 1; fi
	$(eval ENCODED_QUERY=$(shell echo "$(QUERY)" | sed 's/ /+/g'))
	$(eval DATE_PARAM=$(if $(SEARCH_DATE),&date=$(SEARCH_DATE),))
	true > search-prod.json && curl -s -H "Authorization: Bearer $(API_KEY)" "http://localhost:8000/v1/products/?q=$(ENCODED_QUERY)&store_ids=$(STORE_IDS)$(DATE_PARAM)" | jq . > prod.json

## Testing and Logging Commands
# API
test-api: ## Run pytest integration tests for the API service
	@echo "Running API integration tests..."
	
	@echo "Test auth..."
	$(MAKE) test-auth

	@echo "Test Crawler"
	$(MAKE) test-crawler

	@echo "Test stores..."
	$(MAKE) test-stores

	@echo "Test Shopping Lists..."
	$(MAKE) test-shopping-lists

	@echo "Test Chat limun V2..."
	$(MAKE) test-chat-v2

	@echo "Test Chat Eifelov V2..."
	$(MAKE) test-chat-v2 QUERY="Koliko je visok Eifelov toranj?"

test-crawler: ## Run pytest integration tests for the crawler API service
	@echo "Running crawler API integration tests..."
	docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm \
		--env DEBUG=1 \
		--env DB_HOST=db \
		--env DB_PORT=5432 \
		--env POSTGRES_USER=$(POSTGRES_USER) \
		--env POSTGRES_PASSWORD=$(POSTGRES_PASSWORD) \
		--env POSTGRES_DB=$(POSTGRES_DB) \
		api pytest tests/test_crawler.py

test-stores: ## Run pytest integration tests for the stores API service
	@echo "Running stores API integration tests..."
	docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm \
		--env DEBUG=1 \
		--env DB_HOST=db \
		--env DB_PORT=5432 \
		--env POSTGRES_USER=$(POSTGRES_USER) \
		--env POSTGRES_PASSWORD=$(POSTGRES_PASSWORD) \
		--env POSTGRES_DB=$(POSTGRES_DB) \
		api pytest tests/test_stores.py

test-auth: ## Run pytest integration tests for the authentication API service
	@echo "Running authentication API integration tests..."
	docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm \
		--env DEBUG=1 \
		--env DB_HOST=db \
		--env DB_PORT=5432 \
		--env POSTGRES_USER=$(POSTGRES_USER) \
		--env POSTGRES_PASSWORD=$(POSTGRES_PASSWORD) \
		--env POSTGRES_DB=$(POSTGRES_DB) \
		--env JWT_SECRET_KEY=$(JWT_SECRET_KEY) \
		--env JWT_ALGORITHM=$(JWT_ALGORITHM) \
		--env ACCESS_TOKEN_EXPIRE_MINUTES=$(ACCESS_TOKEN_EXPIRE_MINUTES) \
		--env REFRESH_TOKEN_EXPIRE_DAYS=$(REFRESH_TOKEN_EXPIRE_DAYS) \
		--env SMTP_SERVER=$(SMTP_SERVER) \
		--env SMTP_PORT=$(SMTP_PORT) \
		--env SMTP_USERNAME=$(SMTP_USERNAME) \
		--env SMTP_PASSWORD=$(SMTP_PASSWORD) \
		--env SENDER_EMAIL=$(SENDER_EMAIL) \
		--env EMAIL_VERIFICATION_BASE_URL=$(EMAIL_VERIFICATION_BASE_URL) \
		api pytest tests/test_auth.py

test-shopping-lists: ## Run pytest integration tests for the shopping lists API service
	@echo "Running shopping lists API integration tests..."
	docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm \
		--env DEBUG=1 \
		--env DB_HOST=db \
		--env DB_PORT=5432 \
		--env POSTGRES_USER=$(POSTGRES_USER) \
		--env POSTGRES_PASSWORD=$(POSTGRES_PASSWORD) \
		--env POSTGRES_DB=$(POSTGRES_DB) \
		--env JWT_SECRET_KEY=$(JWT_SECRET_KEY) \
		--env JWT_ALGORITHM=$(JWT_ALGORITHM) \
		--env ACCESS_TOKEN_EXPIRE_MINUTES=$(ACCESS_TOKEN_EXPIRE_MINUTES) \
		--env REFRESH_TOKEN_EXPIRE_DAYS=$(REFRESH_TOKEN_EXPIRE_DAYS) \
		--env SMTP_SERVER=$(SMTP_SERVER) \
		--env SMTP_PORT=$(SMTP_PORT) \
		--env SMTP_USERNAME=$(SMTP_USERNAME) \
		--env SMTP_PASSWORD=$(SMTP_PASSWORD) \
		--env SENDER_EMAIL=$(SENDER_EMAIL) \
		--env EMAIL_VERIFICATION_BASE_URL=$(EMAIL_VERIFICATION_BASE_URL) \
		api pytest tests/test_shopping_lists.py

test-chat-v2: ## Run pytest integration tests for the chat v2 API service
	@echo "Running chat v2 API integration tests..."
	docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm \
		--env DEBUG=1 \
		--env DB_HOST=db \
		--env DB_PORT=5432 \
		--env POSTGRES_USER=$(POSTGRES_USER) \
		--env POSTGRES_PASSWORD=$(POSTGRES_PASSWORD) \
		--env POSTGRES_DB=$(POSTGRES_DB) \
		--env JWT_SECRET_KEY=$(JWT_SECRET_KEY) \
		--env JWT_ALGORITHM=$(JWT_ALGORITHM) \
		--env ACCESS_TOKEN_EXPIRE_MINUTES=$(ACCESS_TOKEN_EXPIRE_MINUTES) \
		--env REFRESH_TOKEN_EXPIRE_DAYS=$(REFRESH_TOKEN_EXPIRE_DAYS) \
		--env SMTP_SERVER=$(SMTP_SERVER) \
		--env SMTP_PORT=$(SMTP_PORT) \
		--env SMTP_USERNAME=$(SMTP_USERNAME) \
		--env SMTP_PASSWORD=$(SMTP_PASSWORD) \
		--env SENDER_EMAIL=$(SENDER_EMAIL) \
		--env EMAIL_VERIFICATION_BASE_URL=$(EMAIL_VERIFICATION_BASE_URL) \
		--env API_KEY=$(API_KEY) \
		--env GOOGLE_API_KEY=$(GOOGLE_API_KEY) \
		api pytest tests/test_chat_v2.py -s --query="$(QUERY)"

logs-api: ## Display full logs for the API service
	@echo "Displaying full API logs..."
	docker compose -f docker-compose.yml -f docker-compose.local.yml logs api

# Crawler
logs-crawler: ## Display logs for the Crawler service and save to ./logs/crawler.log (empties file first)
	mkdir -p logs && > ./logs/crawler.log && docker compose -f docker-compose.yml -f docker-compose.local.yml logs crawler > ./logs/crawler.log

logs-tail: ## Continuously display logs from ./logs/api.log
	@if [ "$(IS_WINDOWS)" = "true" ]; then \
		pwsh.exe -Command "Get-Content -Path './logs/api.log' -Wait"; \
	else \
		tail -f './logs/api.log'; \
	fi

logs-crawler-console: ## Continuously display console output from logs/crawler_console.log
	@if [ "$(IS_WINDOWS)" = "true" ]; then \
		pwsh.exe -Command "Get-Content -Path './logs/crawler_console.log' -Wait"; \
	else \
		tail -f './logs/crawler_console.log'; \
	fi

## SSH Commands
ssh-server: ## SSH into the VPS server
	ssh-add ~/.ssh/github_actions_deploy_key; ssh -L 8081:localhost:80 $(SSH_USER)@$(SSH_IP)

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

gpush: ## Add all changes, commit with a message, and push to the remote repository. Usage: make gpush M="Your commit message"
	@if [ -z "$(M)" ]; then echo "Error: M is required. Usage: make gpush M=\"Your commit message\""; exit 1; fi
	git add .
	git commit -m "$(M)"
	git push
