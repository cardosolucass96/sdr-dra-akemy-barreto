#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE_PATH="${ENV_FILE:-.env}"
LANGGRAPH_BIN="${LANGGRAPH_BIN:-$ROOT_DIR/.venv/bin/langgraph}"
LANGGRAPH_CONFIG="${LANGGRAPH_CONFIG:-$ROOT_DIR/langgraph.json}"
BASE_ENV_FILE="$ROOT_DIR/$ENV_FILE_PATH"

if [[ ! -x "$LANGGRAPH_BIN" ]]; then
  echo "langgraph nao encontrado em $LANGGRAPH_BIN. Rode make install primeiro." >&2
  exit 1
fi

if [[ ! -f "$BASE_ENV_FILE" ]]; then
  echo "Arquivo de ambiente nao encontrado: $BASE_ENV_FILE" >&2
  exit 1
fi

echo "LangGraph dev usando $BASE_ENV_FILE"

cd "$ROOT_DIR"
exec "$LANGGRAPH_BIN" dev --config "$LANGGRAPH_CONFIG" --no-browser "$@"
