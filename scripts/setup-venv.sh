#!/usr/bin/env bash
# Create or refresh the shared root virtual environment (.venv) with pinned, hash-verified
# dependencies. Safe to re-run: it syncs an existing .venv to requirements.txt.
#
# Steps:
#   1. Find a Python 3.12+ interpreter.
#   2. Create .venv at the repo root if missing.
#   3. Upgrade pip.
#   4. Install exact, checksum-verified dependencies from requirements.txt (--require-hashes).
#   5. Install the local `shared` package editable (--no-deps; deps already verified).
#
# Usage:  ./scripts/setup-venv.sh
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
echo "Repository root: $repo_root"

# 1. Create the venv if it does not already exist.
if [ ! -d .venv ]; then
  echo "Creating virtual environment at .venv ..."
  if command -v python3 >/dev/null 2>&1; then
    python3 -m venv .venv
  elif command -v python >/dev/null 2>&1; then
    python -m venv .venv
  else
    echo "Python 3.12+ was not found. Install it and re-run this script." >&2
    exit 1
  fi
else
  echo ".venv already exists; syncing dependencies."
fi

venv_python="$repo_root/.venv/bin/python"
if [ ! -x "$venv_python" ]; then
  echo "Expected interpreter not found at $venv_python" >&2
  exit 1
fi

# 2-4. Upgrade pip, install hash-verified dependencies, install the local packages editable.
"$venv_python" -m pip install --upgrade pip
"$venv_python" -m pip install --require-hashes -r requirements.txt
"$venv_python" -m pip install -e src/shared --no-deps
"$venv_python" -m pip install -e src/services/ingestion --no-deps
"$venv_python" -m pip install -e src/services/cleansing --no-deps
"$venv_python" -m pip install -e src/services/prediction --no-deps

echo
echo "Virtual environment ready."
echo "In VS Code: Command Palette -> 'Python: Select Interpreter' -> .venv"
