#!/usr/bin/env bash
# First-time setup: .env, Python (uv) and Node dependencies, pre-commit hooks.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Replace every CHANGE_ME before 'make dev'."
fi

command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/"; exit 1; }
command -v npm >/dev/null || { echo "Node.js/npm is required"; exit 1; }

uv sync --all-packages
(cd frontend && npm install)
uv run pre-commit install
echo "Setup done. Next: make models && make dev"
