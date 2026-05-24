#!/usr/bin/env bash
# RMIFO Dashboard — Build script
# Gera pacotes ZIP para Linux, Windows e macOS com dependências locais pré-baixadas.
#
# Uso:
#   bash build.sh              # gera todos os 3 ZIPs
#   bash build.sh linux        # apenas Linux
#   bash build.sh windows      # apenas Windows
#   bash build.sh macos        # apenas macOS
#   bash build.sh deps         # apenas baixa/atualiza os wheels de dependências
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

NAME="rmifo-dashboard"
MAIN="dashboard.py"
DIST_DIR="dist"
DEPS_BASE="deps"

# Versões fixas usadas no download
MATPLOTLIB="matplotlib==3.10.9"
NUMPY="numpy==2.2.6"
PSYCOPG2="psycopg2-binary==2.9.12"
NETWORKX="networkx==3.6.1"
CORE_PKGS="$MATPLOTLIB $NUMPY $PSYCOPG2 $NETWORKX"

PY=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)

log()  { echo "==> $*"; }
step() { echo ""; echo "──── $* ────"; }

# ─── baixar wheels ────────────────────────────────────────────────────────────
download_deps() {
    step "Baixando dependências para todas as plataformas"

    mkdir -p "$DEPS_BASE/linux" "$DEPS_BASE/windows" "$DEPS_BASE/macos"

    log "Linux (manylinux x86_64, cp312)…"
    pip3 download --quiet \
        --dest "$DEPS_BASE/linux" \
        --platform manylinux_2_17_x86_64 \
        --python-version 3.12 \
        --implementation cp --abi cp312 \
        --only-binary=:all: \
        $CORE_PKGS || log "AVISO: alguns pacotes Linux não encontrados como binários."

    log "Windows (win_amd64, cp312)…"
    pip3 download --quiet \
        --dest "$DEPS_BASE/windows" \
        --platform win_amd64 \
        --python-version 3.12 \
        --implementation cp --abi cp312 \
        --only-binary=:all: \
        $CORE_PKGS || log "AVISO: alguns pacotes Windows não encontrados como binários."

    log "macOS arm64 (Apple Silicon, cp312)…"
    pip3 download --quiet \
        --dest "$DEPS_BASE/macos" \
        --platform macosx_11_0_arm64 \
        --python-version 3.12 \
        --implementation cp --abi cp312 \
        --only-binary=:all: \
        "$MATPLOTLIB" "$NUMPY" "$NETWORKX" \
        || log "AVISO: alguns pacotes macOS arm64 não encontrados."

    log "macOS x86_64 (Intel, cp312)…"
    pip3 download --quiet \
        --dest "$DEPS_BASE/macos" \
        --platform macosx_10_15_x86_64 \
        --python-version 3.12 \
        --implementation cp --abi cp312 \
        --only-binary=:all: \
        "$PSYCOPG2" \
        || log "AVISO: psycopg2 macOS intel não encontrado."

    # Pacotes pure-Python (universais, compartilhados entre plataformas)
    log "Pacotes pure-Python (universal)…"
    pip3 download --quiet \
        --dest "$DEPS_BASE/linux" \
        --only-binary=:all: \
        networkx==3.6.1 packaging pyparsing python-dateutil six cycler \
        2>/dev/null || true

    log "Dependências baixadas."
    for plat in linux windows macos; do
        count=$(ls "$DEPS_BASE/$plat"/*.whl 2>/dev/null | wc -l)
        echo "    $plat: $count wheel(s)"
    done
}

# ─── montar pacote ────────────────────────────────────────────────────────────
build_package() {
    local plat=$1          # linux | windows | macos
    local pkg_dir="$DIST_DIR/$plat"
    local zip_name="${NAME}-${plat}.zip"

    step "Montando pacote $plat"

    rm -rf "$pkg_dir"
    mkdir -p "$pkg_dir/deps"

    # Copia o código-fonte
    cp "$MAIN" "$pkg_dir/"

    # Copia as dependências locais
    if ls "$DEPS_BASE/$plat"/*.whl 2>/dev/null | head -1 > /dev/null; then
        cp "$DEPS_BASE/$plat"/*.whl "$pkg_dir/deps/"
        log "$(ls "$pkg_dir/deps/"*.whl | wc -l) wheels copiados para deps/"
    else
        log "AVISO: nenhum wheel encontrado em $DEPS_BASE/$plat/"
    fi

    # Copia scripts de execução e README
    case "$plat" in
        linux)
            cp "launcher/linux/run.sh"    "$pkg_dir/"
            cp "launcher/linux/README.txt" "$pkg_dir/"
            chmod +x "$pkg_dir/run.sh"
            ;;
        windows)
            cp "launcher/windows/run.bat"    "$pkg_dir/"
            cp "launcher/windows/README.txt" "$pkg_dir/"
            ;;
        macos)
            cp "launcher/macos/run.sh"    "$pkg_dir/"
            cp "launcher/macos/README.txt" "$pkg_dir/"
            chmod +x "$pkg_dir/run.sh"
            ;;
    esac

    # Gera o ZIP
    local zip_path="$DIST_DIR/$zip_name"
    rm -f "$zip_path"
    (cd "$DIST_DIR" && zip -r "../$zip_path" "$plat/" --exclude "*/.venv/*" --exclude "*/__pycache__/*") 2>/dev/null \
        || (cd "$DIST_DIR" && python3 -c "
import zipfile, os, pathlib
plat = '$plat'
z = zipfile.ZipFile('../$zip_path', 'w', zipfile.ZIP_DEFLATED)
for f in pathlib.Path(plat).rglob('*'):
    if '.venv' in f.parts or '__pycache__' in f.parts:
        continue
    z.write(f)
z.close()
print('ZIP criado via Python zipfile.')
")

    SIZE=$(du -sh "$zip_path" 2>/dev/null | cut -f1)
    log "Gerado: $zip_path  ($SIZE)"
}

# ─── main ─────────────────────────────────────────────────────────────────────
mkdir -p "$DIST_DIR"

TARGET="${1:-all}"

case "$TARGET" in
    deps)
        download_deps
        ;;
    linux)
        [[ ! -d "$DEPS_BASE/linux" ]] || [[ -z "$(ls "$DEPS_BASE/linux"/*.whl 2>/dev/null)" ]] && download_deps
        build_package linux
        ;;
    windows)
        [[ ! -d "$DEPS_BASE/windows" ]] || [[ -z "$(ls "$DEPS_BASE/windows"/*.whl 2>/dev/null)" ]] && download_deps
        build_package windows
        ;;
    macos)
        [[ ! -d "$DEPS_BASE/macos" ]] || [[ -z "$(ls "$DEPS_BASE/macos"/*.whl 2>/dev/null)" ]] && download_deps
        build_package macos
        ;;
    all)
        download_deps
        build_package linux
        build_package windows
        build_package macos

        echo ""
        echo "══════════════════════════════════════════════════"
        echo "  Pacotes gerados em dist/:"
        for plat in linux windows macos; do
            f="$DIST_DIR/${NAME}-${plat}.zip"
            [[ -f "$f" ]] && echo "    $(du -sh "$f" | cut -f1)  $f"
        done
        echo "══════════════════════════════════════════════════"
        ;;
    *)
        echo "Uso: bash build.sh [all|linux|windows|macos|deps]"
        exit 1
        ;;
esac
