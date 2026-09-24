#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE_PATH="${ENV_FILE:-.env}"
UVICORN_LOG_LEVEL="${UVICORN_LOG_LEVEL:-warning}"
UVICORN_ACCESS_LOG="${UVICORN_ACCESS_LOG:-false}"
APP_PID=""
TUNNEL_PID=""
source "$ROOT_DIR/scripts/env.sh"

ENV_FILE_PATH="$(resolve_env_file "$ROOT_DIR" "$ENV_FILE_PATH")"

cleanup() {
  local exit_code=${1:-0}

  if [[ -n "$TUNNEL_PID" ]] && kill -0 "$TUNNEL_PID" 2>/dev/null; then
    kill "$TUNNEL_PID" 2>/dev/null || true
    wait "$TUNNEL_PID" 2>/dev/null || true
  fi

  if [[ -n "$APP_PID" ]] && kill -0 "$APP_PID" 2>/dev/null; then
    kill "$APP_PID" 2>/dev/null || true
    wait "$APP_PID" 2>/dev/null || true
  fi

  exit "$exit_code"
}

handle_signal() {
  echo
  echo "Encerrando stack local..."
  cleanup 0
}

trap handle_signal INT TERM

if [[ ! -f "$ENV_FILE_PATH" ]]; then
  echo "Arquivo de ambiente nao encontrado: $ENV_FILE_PATH" >&2
  exit 1
fi

cd "$ROOT_DIR"

UVICORN_RUNTIME_ARGS=(--reload --reload-include ".env*" --log-level "$UVICORN_LOG_LEVEL")
case "${UVICORN_ACCESS_LOG}" in
  true|TRUE|1|yes|YES)
    ;;
  *)
    UVICORN_RUNTIME_ARGS+=(--no-access-log)
    ;;
esac

"$ROOT_DIR/scripts/run_app.sh" "${UVICORN_RUNTIME_ARGS[@]}" &
APP_PID=$!

echo "Subindo tunnel local"
"$ROOT_DIR/scripts/run_dev_tunnel.sh" &
TUNNEL_PID=$!

while true; do
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    wait "$APP_PID"
    APP_STATUS=$?
    echo "API local encerrou com status $APP_STATUS." >&2
    cleanup "$APP_STATUS"
  fi

  if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    wait "$TUNNEL_PID"
    TUNNEL_STATUS=$?
    echo "Tunnel local encerrou com status $TUNNEL_STATUS." >&2
    cleanup "$TUNNEL_STATUS"
  fi

  sleep 1
done
