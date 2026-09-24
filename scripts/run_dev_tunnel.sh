#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE_PATH="${ENV_FILE:-.env}"
source "$ROOT_DIR/scripts/env.sh"

SELECTED_ENV_FILE="$(resolve_env_file "$ROOT_DIR" "$ENV_FILE_PATH")"

cloudflare_config_value() {
  local key="$1"
  local config_file="$2"

  python3 - "$key" "$config_file" <<'PY'
import sys
from pathlib import Path

key = sys.argv[1]
config_file = Path(sys.argv[2]).expanduser()

for line in config_file.read_text().splitlines():
    stripped = line.strip()
    if stripped.startswith(f"{key}:"):
        print(stripped.split(":", 1)[1].strip())
        break
PY
}

cloudflare_config_has_hostname() {
  local hostname="$1"
  local config_file="$2"

  python3 - "$hostname" "$config_file" <<'PY'
import sys
from pathlib import Path

hostname = sys.argv[1]
config_file = Path(sys.argv[2]).expanduser()

for line in config_file.read_text().splitlines():
    stripped = line.strip()
    if stripped.startswith("- hostname:") and stripped.split(":", 1)[1].strip() == hostname:
        raise SystemExit(0)

raise SystemExit(1)
PY
}

pick_metrics_address() {
  local requested="$1"

  python3 - "$requested" <<'PY'
import socket
import sys

requested = sys.argv[1]

if ":" not in requested:
    print(requested)
    raise SystemExit(0)

host, port_text = requested.rsplit(":", 1)
try:
    port = int(port_text)
except ValueError:
    print(requested)
    raise SystemExit(0)

if port == 0:
    print(requested)
    raise SystemExit(0)

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError:
        print(f"{host}:0")
    else:
        print(requested)
PY
}

# Preserve values injected by the shell or deployment platform.
load_env_file "$SELECTED_ENV_FILE" false

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared nao encontrado. Instale o binario antes de subir o tunnel." >&2
  exit 1
fi

TUNNEL_HOSTNAME="${CLOUDFLARE_TUNNEL_HOSTNAME:-}"
TUNNEL_URL="${CLOUDFLARE_TUNNEL_URL:-http://127.0.0.1:8000}"
TUNNEL_METRICS="${CLOUDFLARE_TUNNEL_METRICS:-127.0.0.1:20241}"
TUNNEL_LOGLEVEL="${CLOUDFLARE_TUNNEL_LOGLEVEL:-info}"
TUNNEL_MODE="${CLOUDFLARE_TUNNEL_MODE:-auto}"
TUNNEL_CONFIG_FILE="${CLOUDFLARE_TUNNEL_CONFIG_FILE:-$HOME/.cloudflared/config.yml}"
TUNNEL_NAME="${CLOUDFLARE_TUNNEL_NAME:-}"
HEALTHCHECK_URL="${TUNNEL_URL%/}/health"
RESOLVED_TUNNEL_METRICS="$(pick_metrics_address "$TUNNEL_METRICS")"

if [[ -z "$TUNNEL_HOSTNAME" ]]; then
  echo "Defina CLOUDFLARE_TUNNEL_HOSTNAME em $SELECTED_ENV_FILE antes de subir o tunnel." >&2
  exit 1
fi

if curl --silent --fail --max-time 2 "$HEALTHCHECK_URL" >/dev/null 2>&1; then
  echo "Origin saudavel em $HEALTHCHECK_URL"
else
  echo "Aviso: nao consegui validar $HEALTHCHECK_URL. O tunnel vai subir mesmo assim." >&2
fi

if [[ "$RESOLVED_TUNNEL_METRICS" != "$TUNNEL_METRICS" ]]; then
  echo "Aviso: metrics em $TUNNEL_METRICS ja esta em uso. Vou usar $RESOLVED_TUNNEL_METRICS." >&2
fi

echo "Subindo Cloudflare Tunnel para $TUNNEL_HOSTNAME -> $TUNNEL_URL"

if [[ "$TUNNEL_MODE" != "token" ]] && [[ -f "$TUNNEL_CONFIG_FILE" ]] && cloudflare_config_has_hostname "$TUNNEL_HOSTNAME" "$TUNNEL_CONFIG_FILE"; then
  cloudflared tunnel --config "$TUNNEL_CONFIG_FILE" ingress validate >/dev/null

  if [[ -z "$TUNNEL_NAME" ]]; then
    TUNNEL_NAME="$(cloudflare_config_value "tunnel" "$TUNNEL_CONFIG_FILE")"
  fi

  if [[ -z "$TUNNEL_NAME" ]]; then
    echo "Nao consegui resolver o tunnel em $TUNNEL_CONFIG_FILE." >&2
    exit 1
  fi

  echo "Usando config local em $TUNNEL_CONFIG_FILE"
  echo "Executando tunnel $TUNNEL_NAME com ingress ja configurado para $TUNNEL_HOSTNAME"

  exec cloudflared tunnel \
    --config "$TUNNEL_CONFIG_FILE" \
    --metrics "$RESOLVED_TUNNEL_METRICS" \
    --loglevel "$TUNNEL_LOGLEVEL" \
    --no-autoupdate \
    run "$TUNNEL_NAME"
fi

: "${CLOUDFLARE_TUNNEL_TOKEN:?Defina CLOUDFLARE_TUNNEL_TOKEN em $SELECTED_ENV_FILE para subir o tunnel por token, ou mantenha um ~/.cloudflared/config.yml com a rota local.}"

echo "Usando modo token-based com configuracao remota do painel da Cloudflare."
echo "A rota de $TUNNEL_HOSTNAME deve apontar para $TUNNEL_URL para o webhook chegar na API local."

exec cloudflared tunnel run \
  --no-autoupdate \
  --token "$CLOUDFLARE_TUNNEL_TOKEN" \
  --metrics "$RESOLVED_TUNNEL_METRICS" \
  --loglevel "$TUNNEL_LOGLEVEL"
