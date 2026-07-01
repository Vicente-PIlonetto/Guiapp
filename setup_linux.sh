#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

log() {
  printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*"
}

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    return 1
  fi
}

sudo_cmd() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

try_install() {
  local manager="$1"
  shift
  if "$@"; then
    return 0
  fi
  log "Falha ao instalar via ${manager}. Verifique o gerenciador de pacotes ou instale manualmente."
  return 1
}

install_system_deps() {
  if [[ "${SKIP_SYSTEM_DEPS:-${SKIP_APT:-0}}" == "1" ]]; then
    log "SKIP_SYSTEM_DEPS=1 definido; pulando instalacao de pacotes do sistema."
    return
  fi

  if need_cmd apt-get; then
    log "Instalando dependencias do sistema via apt."
    sudo_cmd apt-get update
    try_install apt sudo_cmd apt-get install -y python3 python3-venv python3-pip build-essential make nodejs npm
    sudo_cmd apt-get install -y firebird-utils || sudo_cmd apt-get install -y firebird3.0-utils || \
      log "Firebird utilities nao foram instaladas automaticamente. Configure gfix/gbak manualmente se usar reparo Firebird."
    return
  fi

  if need_cmd dnf; then
    log "Instalando dependencias do sistema via dnf."
    try_install dnf sudo_cmd dnf install -y python3 python3-pip gcc make nodejs npm
    sudo_cmd dnf install -y firebird-utils || sudo_cmd dnf install -y firebird || \
      log "Firebird utilities nao foram instaladas automaticamente. Configure gfix/gbak manualmente se usar reparo Firebird."
    return
  fi

  if need_cmd yum; then
    log "Instalando dependencias do sistema via yum."
    try_install yum sudo_cmd yum install -y python3 python3-pip gcc make nodejs npm
    sudo_cmd yum install -y firebird-utils || sudo_cmd yum install -y firebird || \
      log "Firebird utilities nao foram instaladas automaticamente. Configure gfix/gbak manualmente se usar reparo Firebird."
    return
  fi

  if need_cmd pacman; then
    log "Instalando dependencias do sistema via pacman."
    try_install pacman sudo_cmd pacman -Sy --needed --noconfirm python python-pip gcc make nodejs npm
    sudo_cmd pacman -S --needed --noconfirm firebird || \
      log "Firebird utilities nao foram instaladas automaticamente. Configure gfix/gbak manualmente se usar reparo Firebird."
    return
  fi

  if need_cmd apk; then
    log "Instalando dependencias do sistema via apk."
    try_install apk sudo_cmd apk add python3 py3-pip gcc musl-dev make nodejs npm
    sudo_cmd apk add firebird || \
      log "Firebird utilities nao foram instaladas automaticamente. Configure gfix/gbak manualmente se usar reparo Firebird."
    return
  fi

  log "Gerenciador de pacotes nao suportado. Instale manualmente: python3, venv, pip, gcc, make, nodejs, npm, gfix e gbak."
}

prepare_env() {
  if [[ ! -f .env ]]; then
    log "Criando .env a partir de config.example.env."
    cp config.example.env .env
  else
    log ".env ja existe; mantendo configuracao atual."
  fi

  mkdir -p storage/uploads storage/processing storage/backups storage/results storage/logs
}

install_python_deps() {
  log "Preparando ambiente Python."
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -m pip install --upgrade pip
  pip install -r requirements.txt
}

check_runtime_bins() {
  log "Verificando binarios principais."
  for bin in python3 make gcc node npm; do
    if need_cmd "$bin"; then
      printf '  %-8s OK (%s)\n' "$bin" "$(command -v "$bin")"
    else
      printf '  %-8s NAO ENCONTRADO\n' "$bin"
    fi
  done

  for bin in gfix gbak; do
    if need_cmd "$bin"; then
      printf '  %-8s OK (%s)\n' "$bin" "$(command -v "$bin")"
    else
      printf '  %-8s NAO ENCONTRADO (necessario apenas para Reparo de Base Firebird)\n' "$bin"
    fi
  done
}

ensure_node_modules() {
  if [[ ! -d "$ROOT_DIR/frontend" ]]; then
    log "Pasta frontend nao encontrada."
    exit 1
  fi
}

install_frontend_deps() {
  log "Instalando dependencias do frontend."
  cd "$ROOT_DIR/frontend"
  npm install
  npm run build
  cd "$ROOT_DIR"
}

build_modules() {
  log "Compilando modulos C."
  make modules
}

print_next_steps() {
  local port
  port="$(grep -E '^APP_PORT=' .env 2>/dev/null | tail -n 1 | cut -d= -f2 || true)"
  port="${port:-8000}"

  cat <<EOF

Setup concluido.

Para iniciar o backend:
  source .venv/bin/activate
  python -m uvicorn backend.main:app --host 0.0.0.0 --port ${port}

Para iniciar o frontend em modo dev:
  cd frontend
  VITE_API_BASE=http://SEU_IP:${port} npm run dev -- --host 0.0.0.0 --port 5173

Para usar a TUI:
  source .venv/bin/activate
  python tui.py

Para expor com Tailscale Funnel:
  tailscale funnel 5173

EOF
}

install_system_deps
check_runtime_bins
prepare_env
install_python_deps
build_modules
ensure_node_modules
install_frontend_deps
print_next_steps
