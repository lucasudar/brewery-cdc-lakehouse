SHELL := /bin/bash
COMPOSE := docker compose

.DEFAULT_GOAL := help
.PHONY: help up down reset ps logs topics test lint

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

up: ## Build and start the whole pipeline (waits for health checks)
	$(COMPOSE) up -d --build --wait --wait-timeout 300

down: ## Stop everything, keep state (topics, offsets, Iceberg tables)
	$(COMPOSE) down

reset: ## Stop everything and wipe all state (Postgres re-seeds on next `make up`)
	$(COMPOSE) down -v --remove-orphans

ps: ## Show service status
	$(COMPOSE) ps -a

logs: ## Tail logs of all services
	$(COMPOSE) logs -f --tail=100

topics: ## List CDC topics with message counts
	@$(COMPOSE) exec -T redpanda rpk topic list

test: ## Run unit + integration tests (no Docker needed)
	cd services/ingestor && uv run --frozen pytest -q

lint: ## Ruff lint + format check
	cd services/ingestor && uv run --frozen ruff check src tests && uv run --frozen ruff format --check src tests
