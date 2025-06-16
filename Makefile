DATE ?= $(shell date +%Y-%m-%d)

.PHONY: help crawl-sample rebuild rebuild-api import-data add-user search-products logs-api logs-tail

help: ## Display this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | sed -E 's/^(.*?):.*?## (.*)$$/\x1b[36m\1\x1b[0m              \2/'

crawl-sample: ## Run a sample crawl for Lidl and Konzum
	docker-compose run --rm crawler python crawler/cli/crawl.py /app/output --chain lidl,konzum

crawl-all: ## Crawl all data
	docker-compose run --rm crawler python crawler/cli/crawl.py /app/output

rebuild: ## Rebuild and restart all Docker containers
	docker-compose up -d --build --force-recreate

rebuild-api: ## Rebuild and restart only the API service
	docker-compose up -d --build --force-recreate api

import-data: ## Import crawled data for a specific DATE (defaults to today)
	docker-compose run --rm crawler python service/db/import.py /app/output/$(DATE)

add-user: ## Add a new user with a generated API key. Usage: make add-user USERNAME=your_username
	@if [ -z "$(USERNAME)" ]; then echo "Error: USERNAME is required. Usage: make add-user USERNAME=your_username"; exit 1; fi
	docker-compose run --rm api python service/cli/add_user.py $(USERNAME)

STORE_IDS ?= 107,616
QUERY ?= kokos
API_KEY ?= ec7cc315-c434-4c1f-aab7-3dba3545d113
SEARCH_DATE ?= 2025-06-08
search-products: ## Search for products by name. Usage: make search-products QUERY="your query" API_KEY=your_api_key [STORE_IDS=val] [SEARCH_DATE=val]
	@if [ -z "$(API_KEY)" ]; then echo "Error: API_KEY is required. Usage: make search-products API_KEY=your_api_key [QUERY=your_query]"; exit 1; fi
	$(eval ENCODED_QUERY=$(shell echo "$(QUERY)" | sed 's/ /+/g'))
	true > search-prod.json && curl -s -H "Authorization: Bearer $(API_KEY)" "http://localhost:8000/v1/products/?q=$(ENCODED_QUERY)&store_ids=$(STORE_IDS)&date=$(SEARCH_DATE)" | jq . > search-prod.json

LIMIT ?= 100
PRODUCT_NAME_FILTER ?= kokos
search-keywords: ## Get products to send to AI for keywording. Usage: make search-keywords API_KEY=your_api_key [LIMIT=val] [PRODUCT_NAME_FILTER=val]
	@if [ -z "$(API_KEY)" ]; then echo "Error: API_KEY is required. Usage: make search-keywords API_KEY=your_api_key"; exit 1; fi
	$(eval FILTER_PARAM=$(if $(PRODUCT_NAME_FILTER),&product_name_filter=$(PRODUCT_NAME_FILTER),))
	true > prod.json && curl -s -H "Authorization: Bearer $(API_KEY)" "http://localhost:8000/v1/search-keywords/?limit=$(LIMIT)$(FILTER_PARAM)" | jq . > prod.json

logs-api: ## Display logs for the API service and save to ./logs/api.log (empties file first)
	mkdir -p logs && > ./logs/api.log && docker-compose logs api > ./logs/api.log

logs-tail: ## Continuously display logs from ./logs/api.log (PowerShell only)
	pwsh.exe -Command "Get-Content -Path './logs/api.log' -Wait"

geocode-stores: ## Geocode stores in the database that are missing latitude/longitude
	docker-compose run --rm api python -c "import asyncio; from service.cli.geocode_stores import geocode_stores; asyncio.run(geocode_stores())"

LATITUDE ?= 45.29278835973543
LONGITUDE ?= 18.791376990006086
RADIUS ?= 1500
test-nearby: ## Test the nearby stores endpoint. Usage: make test-nearby [LATITUDE=val] [LONGITUDE=val] [RADIUS=val] API_KEY=your_api_key
	@if [ -z "$(API_KEY)" ]; then echo "Error: API_KEY is required. Usage: make test-nearby API_KEY=your_api_key"; exit 1; fi
	curl -s -H "Authorization: Bearer $(API_KEY)" "http://localhost:8000/v1/stores/nearby/?latitude=$(LATITUDE)&longitude=$(LONGITUDE)&radius_meters=$(RADIUS)" | jq .
