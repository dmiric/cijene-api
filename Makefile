DATE ?= $(shell date +%Y-%m-%d)

.PHONY: help crawl-sample rebuild import-data

help: ## Display this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | sed -E 's/^(.*?):.*?## (.*)$$/\x1b[36m\1\x1b[0m              \2/'

crawl-sample: ## Run a sample crawl for Lidl and Konzum
	docker-compose run --rm crawler python crawler/cli/crawl.py /app/output --chain lidl,konzum

rebuild: ## Rebuild and restart all Docker containers
	docker-compose up -d --build --force-recreate

import-data: ## Import crawled data for a specific DATE (defaults to today)
	docker-compose run --rm crawler python service/db/import.py /app/output/$(DATE)
