#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PYTHON_VENV:-${REPO_ROOT}/.venv}"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: npm run generate

Prepare the extractor environment and rebuild pokemon.db.

Environment:
  PYTHON_VENV  Python virtualenv path. Defaults to .venv.

Output:
  ${REPO_ROOT}/pokemon.db
EOF
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required to run the extractor pipeline." >&2
  exit 1
fi

if ! command -v rgbgfx >/dev/null 2>&1; then
  cat >&2 <<'EOF'
rgbgfx is required to convert Pokemon Red/Blue tilesets.

Install RGBDS first:
  macOS:         brew install rgbds
  Ubuntu/Debian: sudo apt-get install rgbds
EOF
  exit 1
fi

cd "${REPO_ROOT}"

echo "Updating pokemon-game-data submodule..."
git submodule update --init --recursive

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "Creating Python virtual environment at ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi

echo "Installing extractor Python dependencies..."
"${VENV_DIR}/bin/python" -m pip install -q -r "${REPO_ROOT}/requirements.txt"

echo "Rebuilding pokemon.db..."
"${VENV_DIR}/bin/python" "${REPO_ROOT}/export_scripts/reprocess.py"

echo "Extractor data generation complete."
echo "Output: ${REPO_ROOT}/pokemon.db"
