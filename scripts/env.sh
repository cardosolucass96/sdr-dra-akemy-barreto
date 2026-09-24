#!/usr/bin/env bash

resolve_env_file() {
  local root_dir="$1"
  local env_file="$2"

  if [[ "$env_file" = /* ]]; then
    printf '%s\n' "$env_file"
  else
    printf '%s\n' "$root_dir/$env_file"
  fi
}

load_env_file() {
  local env_file="$1"
  local override_existing="${2:-true}"
  [[ -f "$env_file" ]] || return 0

  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"

    [[ -z "${line//[[:space:]]/}" ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" == *"="* ]] || continue

    local key="${line%%=*}"
    local value="${line#*=}"

    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    value="${value#"${value%%[![:space:]]*}"}"

    if [[ "$value" =~ ^\".*\"$ || "$value" =~ ^\'.*\'$ ]]; then
      value="${value:1:-1}"
    fi

    if [[ "$override_existing" != "true" ]] && printenv "$key" >/dev/null 2>&1; then
      continue
    fi

    export "$key=$value"
  done < "$env_file"
}
