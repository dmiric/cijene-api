DATE ?= $(shell date +%Y-%m-%d)

.PHONY: help crawl-sample rebuild import-data add-user search-products logs-api

help: ## Display this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | sed -E 's/^(.*?):.*?## (.*)$$/\x1b[36m\1\x1b[0m              \2/'

crawl-sample: ## Run a sample crawl for Lidl and Konzum
	docker-compose run --rm crawler python crawler/cli/crawl.py /app/output --chain lidl,konzum

crawl-all: ## Crawl all data
	docker-compose run --rm crawler python crawler/cli/crawl.py /app/output

rebuild: ## Rebuild and restart all Docker containers
	docker-compose up -d --build --force-recreate

import-data: ## Import crawled data for a specific DATE (defaults to today)
	docker-compose run --rm crawler python service/db/import.py /app/output/$(DATE)

add-user: ## Add a new user with a generated API key. Usage: make add-user USERNAME=your_username
	@if [ -z "$(USERNAME)" ]; then echo "Error: USERNAME is required. Usage: make add-user USERNAME=your_username"; exit 1; fi
	docker-compose run --rm api python service/cli/add_user.py $(USERNAME)

QUERY ?= kokos
API_KEY ?= ec7cc315-c434-4c1f-aab7-3dba3545d113
search-products: ## Search for products by name. Usage: make search-products QUERY="your query" API_KEY=your_api_key
	@if [ -z "$(API_KEY)" ]; then echo "Error: API_KEY is required. Usage: make search-products API_KEY=your_api_key [QUERY=your_query]"; exit 1; fi
	$(eval ENCODED_QUERY=$(shell echo "$(QUERY)" | sed 's/ /+/g'))
	curl -s -H "Authorization: Bearer $(API_KEY)" "http://localhost:8000/v1/products/?q=$(ENCODED_QUERY)" | jq .

LIMIT ?= 100
search-keywords: ## Get search keywords for AI
	@if [ -z "$(API_KEY)" ]; then echo "Error: API_KEY is required. Usage: make search-keywords API_KEY=your_api_key"; exit 1; fi
	curl -s -H "Authorization: Bearer $(API_KEY)" "http://localhost:8000/v1/search-keywords/?limit=$(LIMIT)" | jq .

logs-api: ## Display logs for the API service and save to ./logs/api.log (empties file first)
	mkdir -p logs && > ./logs/api.log && docker-compose logs api > ./logs/api.log
