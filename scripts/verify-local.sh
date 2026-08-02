#!/usr/bin/env bash
# Offline Remedi gate — fixture mode (no DataHub GMS required).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
unset VIRTUAL_ENV || true

echo "==> uv sync"
uv sync --extra dev --extra live

echo "==> pytest"
uv run pytest -q

echo "==> ruff"
uv run ruff check src tests scripts/*.py
uv run ruff format --check src tests scripts/*.py

echo "==> generated artifact lint + format"
uv run ruff check examples/generated --select E,F,I
uv run ruff format --check examples/generated

echo "==> selftest"
uv run remedi selftest

echo "==> verify-local OK"
echo "Offline verification passed. This is not live DataHub evidence."
echo "Next: set REMEDI_API_KEY, connect DataHub, and run 'make serve-live'."
