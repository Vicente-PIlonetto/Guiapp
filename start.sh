#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PORT="${APP_PORT:-8000}"

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
  PORT="${APP_PORT:-$PORT}"
fi

if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

if [ -d "/opt/firebird251/root/opt/firebird" ]; then
  export FIREBIRD="${FIREBIRD:-/opt/firebird251/root/opt/firebird}"
  export LD_LIBRARY_PATH="$FIREBIRD/lib:${LD_LIBRARY_PATH:-}"
fi

if [ "${NO_TAILSCALE_SETUP:-0}" = "1" ]; then
  echo "Configuracao do Tailscale ignorada neste processo."
elif command -v tailscale >/dev/null 2>&1; then
  echo "Configurando Tailscale Funnel na porta $PORT..."
  sudo tailscale funnel reset >/dev/null 2>&1 || true
  sudo tailscale funnel --bg --yes --https=443 "http://127.0.0.1:$PORT"
else
  echo "tailscale nao encontrado; iniciando apenas o servidor local."
fi

echo "Iniciando GUINAPP em http://0.0.0.0:$PORT"
exec python -m uvicorn backend.main:app --host 0.0.0.0 --port "$PORT"
