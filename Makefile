.PHONY: help up down dev-backend dev-frontend test lint typecheck check

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

up: ## Build and start the full stack with docker compose
	docker compose up --build

down: ## Stop the stack
	docker compose down

dev-backend: ## Run the API with auto-reload on :8000
	cd backend && uv run uvicorn callpilot.main:app --reload --port 8000

dev-frontend: ## Run the web app with hot reload on :3000
	cd frontend && npm run dev

test: ## Run backend tests
	cd backend && uv run pytest

lint: ## Lint backend and frontend
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run lint

typecheck: ## Type-check backend and frontend
	cd backend && uv run mypy
	cd frontend && npm run typecheck

check: lint typecheck test ## Everything CI runs
