#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE_PATH="${ENV_FILE:-.env}"
UVICORN_BIN="${UVICORN_BIN:-$ROOT_DIR/.venv/bin/uvicorn}"
source "$ROOT_DIR/scripts/env.sh"

BASE_ENV_FILE="$(resolve_env_file "$ROOT_DIR" "$ENV_FILE_PATH")"

if [[ ! -x "$UVICORN_BIN" ]]; then
  echo "uvicorn nao encontrado em $UVICORN_BIN. Rode make install primeiro." >&2
  exit 1
fi

# Preserve values injected by the shell or deployment platform.
load_env_file "$BASE_ENV_FILE" false

if [[ -f "$BASE_ENV_FILE" ]]; then
  echo "Uvicorn usando $BASE_ENV_FILE"
else
  echo "Uvicorn usando secrets injetados pelo ambiente"
fi

cd "$ROOT_DIR"
exec "$UVICORN_BIN" app.main:app "$@"
