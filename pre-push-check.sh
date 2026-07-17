#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venv_bin="$project_dir/.venv/bin"

if [[ ! -x "$venv_bin/ruff" || ! -x "$venv_bin/mypy" || ! -x "$venv_bin/pytest" ]]; then
    echo "Development tools are missing. Run: .venv/bin/pip install -r requirements-dev.txt" >&2
    exit 1
fi

cd "$project_dir"

echo "Running Ruff lint..."
"$venv_bin/ruff" check .

echo "Checking Ruff formatting..."
"$venv_bin/ruff" format --check .

echo "Running mypy..."
"$venv_bin/mypy"

echo "Running tests with coverage..."
"$venv_bin/pytest" --cov --cov-report=term-missing

echo "Pre-push checks passed."
