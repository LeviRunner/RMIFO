#!/usr/bin/env bash
# RMIFO Dashboard — Linux launcher
# deps/ contém wheels para Python 3.12 (offline).
# Para outras versões de Python, instala automaticamente via PyPI.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# deps/ pode estar na mesma pasta (ZIP) ou em ../../deps/linux/ (repo)
DEPS_DIR="$SCRIPT_DIR/deps"
[[ ! -d "$DEPS_DIR" ]] && DEPS_DIR="$SCRIPT_DIR/../../deps/linux"

# dashboard.py pode estar na mesma pasta (ZIP) ou em ../../ (repo)
MAIN="$SCRIPT_DIR/dashboard.py"
[[ ! -f "$MAIN" ]] && MAIN="$SCRIPT_DIR/../../dashboard.py"
if [[ ! -f "$MAIN" ]]; then
  echo "[RMIFO] dashboard.py não encontrado."
  echo "  Esperado em: $SCRIPT_DIR/dashboard.py"
  echo "  ou em:       $SCRIPT_DIR/../../dashboard.py"
  exit 1
fi

# ── Verifica Python 3.10+ ────────────────────────────────────────────────────
PY=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)
if [[ -z "$PY" ]]; then
  echo "[RMIFO] Python não encontrado."
  echo "  Instale com: sudo apt install python3 python3-venv python3-pip python3-tk"
  exit 1
fi

PY_VER=$("$PY" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [[ "$PY_MAJOR" -lt 3 || ( "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10 ) ]]; then
  echo "[RMIFO] Python $PY_VER detectado — requer 3.10 ou superior."
  exit 1
fi
echo "[RMIFO] Python $PY_VER detectado."

# ── Garante python3-venv (Debian/Ubuntu) ─────────────────────────────────────
if ! "$PY" -c "import ensurepip" &>/dev/null; then
  VENV_PKG="python${PY_VER}-venv"
  echo "[RMIFO] Módulo venv/ensurepip não disponível ($VENV_PKG)."
  if command -v apt-get &>/dev/null; then
    if [[ "$EUID" -eq 0 ]]; then
      echo "[RMIFO] Instalando $VENV_PKG automaticamente…"
      apt-get install -y "$VENV_PKG" 2>/dev/null \
        || apt-get install -y python3-venv python3-pip
    else
      echo "  Execute primeiro:"
      echo "    sudo apt install $VENV_PKG python3-pip python3-tk"
      exit 1
    fi
  else
    echo "  Instale o pacote venv para Python $PY_VER e tente novamente."
    exit 1
  fi
fi

# ── Cria venv ────────────────────────────────────────────────────────────────
if [[ ! -d "$VENV_DIR" ]]; then
  echo "[RMIFO] Criando ambiente virtual em $VENV_DIR …"
  "$PY" -m venv "$VENV_DIR"
fi

PY_VENV="$VENV_DIR/bin/python"

# ── Garante pip dentro do venv (Debian omite pip no venv por padrão) ─────────
if ! "$PY_VENV" -m pip --version &>/dev/null 2>&1; then
  echo "[RMIFO] pip não encontrado no venv — inicializando…"
  # Tenta ensurepip primeiro
  if "$PY_VENV" -m ensurepip --upgrade 2>/dev/null; then
    "$PY_VENV" -m pip install --quiet --upgrade pip
  elif command -v apt-get &>/dev/null && [[ "$EUID" -eq 0 ]]; then
    apt-get install -y python3-pip 2>/dev/null
    "$PY_VENV" -m ensurepip --upgrade 2>/dev/null || true
  else
    echo "[RMIFO] pip não disponível no venv."
    echo "  Execute: sudo apt install python3-pip"
    echo "  Depois remova .venv/ e tente novamente: rm -rf '$VENV_DIR'"
    exit 1
  fi
fi

# ── Instala dependências (local cp312 ou PyPI com fallback) ──────────────────
install_with_pg_fallback() {
  local extra=("$@")
  if "$PY_VENV" -m pip install --quiet --only-binary=:all: \
      "${extra[@]}" matplotlib numpy psycopg2-binary networkx 2>/dev/null; then
    echo "[RMIFO] Dependências instaladas (incluindo suporte a banco de dados)."
  else
    echo "[RMIFO] psycopg2-binary indisponível para Python $PY_VER — modo demo ativo."
    "$PY_VENV" -m pip install --quiet --only-binary=:all: \
      "${extra[@]}" matplotlib numpy networkx
  fi
}

if [[ -d "$DEPS_DIR" && "$PY_MINOR" == "12" && "$PY_MAJOR" == "3" ]]; then
  echo "[RMIFO] Instalando dependências locais offline (Python 3.12)…"
  if ! "$PY_VENV" -m pip install --quiet --only-binary=:all: \
      --find-links "$DEPS_DIR" --no-index \
      matplotlib numpy psycopg2-binary networkx 2>/dev/null; then
    echo "[RMIFO] Wheels locais incompatíveis — tentando PyPI…"
    install_with_pg_fallback "--find-links" "$DEPS_DIR"
  fi
elif [[ -d "$DEPS_DIR" ]]; then
  echo "[RMIFO] Python $PY_VER — wheels locais são cp312. Instalando via PyPI (deps/ como cache)…"
  install_with_pg_fallback "--find-links" "$DEPS_DIR"
else
  echo "[RMIFO] Instalando dependências via PyPI…"
  install_with_pg_fallback
fi

echo "[RMIFO] Iniciando dashboard …"
exec "$PY_VENV" "$MAIN" "$@"
