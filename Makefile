SHELL := /bin/bash
COMPOSE := docker compose
UV_IMAGE := ghcr.io/astral-sh/uv:0.9.5-python3.12-bookworm-slim
PLAYWRIGHT_IMAGE := mcr.microsoft.com/playwright/python:v1.55.0-noble

# Run `uv` for one project inside a container: only Docker is needed on the host.
# The venv and uv cache live in named volumes, so repeated runs take a few seconds.
define uv
	docker run --rm -v "$(CURDIR)":/repo -w /repo/$(1) \
	  -e UV_PROJECT_ENVIRONMENT=/venv -e UV_LINK_MODE=copy -e UV_PYTHON_DOWNLOADS=never \
	  -v lakehouse-venv-$(subst /,-,$(1)):/venv -v lakehouse-uv-cache:/root/.cache/uv \
	  $(UV_IMAGE) sh -c "uv sync --frozen -q && $(2)"
endef

.DEFAULT_GOAL := help
.PHONY: help up down reset demo ps logs topics test lint dbt-build diagrams

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

up: ## Build and start the whole pipeline (waits for health checks)
	$(COMPOSE) up -d --build --wait --wait-timeout 300

down: ## Stop everything, keep state (topics, offsets, Iceberg tables)
	$(COMPOSE) down

reset: ## Stop everything and wipe all state (Postgres re-seeds on next `make up`)
	$(COMPOSE) down -v --remove-orphans

demo: ## Apply scripts/simulate_changes.sql and show the changes reaching the API (measures latency)
	./scripts/demo.sh

ps: ## Show service status
	$(COMPOSE) ps -a

logs: ## Tail logs of all services
	$(COMPOSE) logs -f --tail=100

topics: ## List Kafka topics (CDC, DLQ, Connect internals)
	@$(COMPOSE) exec -T redpanda rpk topic list

test: ## Run unit + integration tests (in a container; no local Python needed)
	$(call uv,services/ingestor,uv run --frozen pytest -q)
	$(call uv,services/api,uv run --frozen pytest -q)

lint: ## Ruff lint + format check (in a container)
	$(call uv,services/ingestor,uv run --frozen ruff check src tests && uv run --frozen ruff format --check src tests)
	$(call uv,services/api,uv run --frozen ruff check src tests && uv run --frozen ruff format --check src tests)
	$(call uv,transform,uv run --frozen ruff check src && uv run --frozen ruff format --check src)

dbt-build: ## Run dbt models + data tests inside the transformer container (current snapshots)
	$(COMPOSE) exec -e MART_OUTPUT_DIR=/tmp/adhoc transformer sh -c "mkdir -p /tmp/adhoc && dbt build --profiles-dir ."

diagrams: ## Regenerate docs/diagrams (draw.io source + PNGs, with layout check) in a Playwright container
	docker run --rm -v "$(CURDIR)":/w -w /w $(PLAYWRIGHT_IMAGE) sh -c \
	  'pip install -q playwright==1.55.0 && python3 docs/diagrams/gen.py && python3 docs/diagrams/render_png.py'
