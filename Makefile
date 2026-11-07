# FaceScan developer commands. Requires uv, node/npm, docker, GNU make.
PY_PATHS := backend ai database scripts

.PHONY: setup dev stop lint format test models licences

setup:
	bash scripts/bootstrap.sh

dev:
	docker compose up --build -d

stop:
	docker compose down

lint:
	uv run ruff check $(PY_PATHS)
	uv run ruff format --check $(PY_PATHS)
	uv run mypy backend/app ai/src
	cd frontend && npm run lint && npm run typecheck

format:
	uv run ruff format $(PY_PATHS)
	uv run ruff check --fix $(PY_PATHS)
	cd frontend && npm run format

test:
	cd backend && uv run pytest
	cd ai && uv run pytest
	cd frontend && npm test

models:
	uv run python ai/scripts/download_models.py

licences:
	uv run python scripts/check_licences.py
