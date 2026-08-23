#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_VENV_DIR="${REPO_ROOT}/.venv"
VENV_DIR="${PYTHON_VENV:-${DEFAULT_VENV_DIR}}"
MANAGED_VENV=0
WITH_AUDIO=0
if [[ -z "${PYTHON_VENV:-}" ]]; then
  MANAGED_VENV=1
fi

show_help() {
  cat <<EOF
Usage: npm run generate -- [--with-audio]

Prepare the extractor environment, rebuild pokemon.db, and generate offline
viewer data/assets. Add --with-audio to render and atomically publish all 561
FLAC masters and Ogg Vorbis distribution files as part of the same release.

Environment:
  PYTHON_VENV  Python virtualenv path. Defaults to .venv.

Output:
  ${REPO_ROOT}/pokemon.db
  ${REPO_ROOT}/build/graphics/
  ${REPO_ROOT}/build/audio/       (only with --with-audio)
  ${REPO_ROOT}/pokemon-phaser/public/viewer-data/
  ${REPO_ROOT}/pokemon-phaser/public/viewer-assets/
EOF
}

for argument in "$@"; do
  case "${argument}" in
    --with-audio)
      WITH_AUDIO=1
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    *)
      echo "Unknown argument: ${argument}" >&2
      show_help >&2
      exit 2
      ;;
  esac
done

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

if [[ "${WITH_AUDIO}" -eq 1 ]]; then
  for AUDIO_TOOL in rgbasm rgblink gbsplay ffmpeg; do
    if ! command -v "${AUDIO_TOOL}" >/dev/null 2>&1; then
      echo "${AUDIO_TOOL} is required for --with-audio." >&2
      exit 1
    fi
  done
fi

cd "${REPO_ROOT}"

echo "Updating pokemon-game-data submodule..."
git submodule update --init --recursive

python_has_dependencies() {
  "$1" - <<'PY' >/dev/null 2>&1
from PIL import Image
PY
}

PYTHON_BIN=""
USING_SYSTEM_PYTHON=0
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "Creating Python virtual environment at ${VENV_DIR}"
  if python3 -m venv "${VENV_DIR}"; then
    PYTHON_BIN="${VENV_DIR}/bin/python"
  else
    echo "Could not create a Python virtual environment; falling back to system python3."
    echo "For isolated installs on Ubuntu/Debian, install python3-venv, e.g. python3.14-venv for Python 3.14."
  fi
else
  PYTHON_BIN="${VENV_DIR}/bin/python"
fi

if [[ -n "${PYTHON_BIN}" ]] && ! "${PYTHON_BIN}" -m pip --version >/dev/null 2>&1; then
  echo "Installing pip into ${VENV_DIR}"
  if ! "${PYTHON_BIN}" -m ensurepip --upgrade >/dev/null 2>&1; then
    if [[ "${MANAGED_VENV}" -eq 1 && "${VENV_DIR}" == "${DEFAULT_VENV_DIR}" ]]; then
      echo "Managed virtual environment is missing pip; recreating ${VENV_DIR}"
      rm -rf -- "${DEFAULT_VENV_DIR}"
      if python3 -m venv "${DEFAULT_VENV_DIR}" && "${DEFAULT_VENV_DIR}/bin/python" -m ensurepip --upgrade >/dev/null 2>&1; then
        PYTHON_BIN="${DEFAULT_VENV_DIR}/bin/python"
      else
        echo "Could not create a pip-enabled virtual environment; falling back to system python3."
        echo "For isolated installs on Ubuntu/Debian, install python3-venv, e.g. python3.14-venv for Python 3.14."
        PYTHON_BIN=""
      fi
    else
      cat >&2 <<EOF
The caller-provided PYTHON_VENV is missing pip and could not be repaired:
  ${VENV_DIR}

It was left untouched. Repair it, choose a different PYTHON_VENV, or unset
PYTHON_VENV so this tool can manage ${DEFAULT_VENV_DIR}.
EOF
      exit 1
    fi
  fi
fi

if [[ -z "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3)"
  USING_SYSTEM_PYTHON=1
fi

if [[ "${USING_SYSTEM_PYTHON}" -eq 1 ]] && python_has_dependencies "${PYTHON_BIN}"; then
  echo "Using ${PYTHON_BIN}; required Python dependencies are already installed."
elif "${PYTHON_BIN}" -m pip --version >/dev/null 2>&1; then
  echo "Installing extractor Python dependencies..."
  "${PYTHON_BIN}" -m pip install -q -r "${REPO_ROOT}/requirements.txt"
elif ! python_has_dependencies "${PYTHON_BIN}"; then
  cat >&2 <<EOF
Python dependencies are missing and pip is not available for ${PYTHON_BIN}.

Install pip/venv support, then rerun:
  Ubuntu/Debian: sudo apt-get install python3-pip python3-venv

For Python 3.14 packages on Ubuntu/Debian, the venv package may be named:
  sudo apt-get install python3.14-venv
EOF
  exit 1
else
  echo "Using ${PYTHON_BIN}; required Python dependencies are already installed."
fi

echo "Rebuilding pokemon.db..."
REPROCESS_ARGS=()
if [[ "${WITH_AUDIO}" -eq 1 ]]; then
  REPROCESS_ARGS+=(--with-audio)
fi
"${PYTHON_BIN}" "${REPO_ROOT}/export_scripts/reprocess.py" "${REPROCESS_ARGS[@]}"

echo "Extractor data generation complete."
echo "Output: ${REPO_ROOT}/pokemon.db"
echo "Graphics: ${REPO_ROOT}/build/graphics"
if [[ "${WITH_AUDIO}" -eq 1 ]]; then
  echo "Audio: ${REPO_ROOT}/build/audio"
fi
echo "Viewer data: ${REPO_ROOT}/pokemon-phaser/public/viewer-data"
echo "Viewer assets: ${REPO_ROOT}/pokemon-phaser/public/viewer-assets"
