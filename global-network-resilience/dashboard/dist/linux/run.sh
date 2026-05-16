#!/usr/bin/env bash
# RMIFO Dashboard — Linux launcher
# Cria um venv isolado, instala dependências locais e executa o dashboard.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
DEPS_DIR="$SCRIPT_DIR/deps"
MAIN="$SCRIPT_DIR/dashboard.py"

# Verifica Python 3.10+
PY=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)
if [[ -z "$PY" ]]; then
  echo "[RMIFO] Python 3.10+ não encontrado."
  echo "  Instale com: sudo apt install python3 python3-venv python3-tk"
  exit 1
fi

PY_VER=$("$PY" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [[ "$PY_MAJOR" -lt 3 || ( "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10 ) ]]; then
  echo "[RMIFO] Python $PY_VER detectado — requer 3.10 ou superior."
  exit 1
fi

# Cria venv se não existir
if [[ ! -d "$VENV_DIR" ]]; then
  echo "[RMIFO] Criando ambiente virtual em $VENV_DIR …"
  "$PY" -m venv "$VENV_DIR"
fi

PY_VENV="$VENV_DIR/bin/python"

# Instala dependências do diretório local (sem internet)
if [[ -d "$DEPS_DIR" ]]; then
  echo "[RMIFO] Instalando dependências locais de $DEPS_DIR …"
  "$PY_VENV" -m pip install --quiet \
    --find-links "$DEPS_DIR" \
    --no-index \
    matplotlib numpy psycopg2-binary networkx
else
  echo "[RMIFO] deps/ não encontrado — instalando via PyPI …"
  "$PY_VENV" -m pip install --quiet \
    matplotlib numpy psycopg2-binary networkx
fi

echo "[RMIFO] Iniciando dashboard …"
exec "$PY_VENV" "$MAIN" "$@"
