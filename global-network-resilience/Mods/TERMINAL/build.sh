#!/usr/bin/env bash
# Build cross-platform executable with PyInstaller
# Usage: bash build.sh [linux|windows|macos]
set -euo pipefail

SCRIPT=dashboard.py
NAME=rmifo-dashboard
OUT=dist

echo "==> Instalando dependências..."
pip install -q -r requirements.txt

PLATFORM=${1:-$(python3 -c "import sys; print(sys.platform)")}

case "$PLATFORM" in
  linux*)
    echo "==> Build Linux (onefile)..."
    pyinstaller --onefile --windowed --name "$NAME" \
      --hidden-import="psycopg2" \
      --hidden-import="matplotlib.backends.backend_tkagg" \
      --hidden-import="networkx" \
      "$SCRIPT"
    echo "==> Executável: $OUT/$NAME"
    ;;
  win*|windows)
    echo "==> Build Windows..."
    pyinstaller --onefile --windowed --name "${NAME}.exe" \
      --hidden-import="psycopg2" \
      --hidden-import="matplotlib.backends.backend_tkagg" \
      --hidden-import="networkx" \
      "$SCRIPT"
    echo "==> Executável: $OUT/${NAME}.exe"
    ;;
  darwin*|macos)
    echo "==> Build macOS (.app)..."
    pyinstaller --onefile --windowed --name "$NAME" \
      --osx-bundle-identifier="br.rmifo.dashboard" \
      --hidden-import="psycopg2" \
      --hidden-import="matplotlib.backends.backend_tkagg" \
      --hidden-import="networkx" \
      "$SCRIPT"
    echo "==> App: $OUT/$NAME.app"
    ;;
  *)
    echo "Plataforma não reconhecida: $PLATFORM"
    exit 1
    ;;
esac

echo "==> Concluído."
