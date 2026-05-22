#!/usr/bin/env bash
# start.sh — entrypoint Railway
# Aplica schema + seed e sobe PHP server.
# NÃO usa set -e: erros SQL individuais são logados mas não param o deploy.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="$SCRIPT_DIR/db/schema.sql"
SEED="$SCRIPT_DIR/db/seed.sql"

echo "[RMIFO] Iniciando deploy..."

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "[RMIFO] DATABASE_URL não definido — pulando schema/seed."
else
    # Aguarda o PostgreSQL aceitar conexões (até 60 s)
    echo "[RMIFO] Aguardando banco de dados..."
    for i in $(seq 1 20); do
        if psql "$DATABASE_URL" -c "SELECT 1" > /dev/null 2>&1; then
            echo "[RMIFO] Banco disponível após ${i} tentativa(s)."
            break
        fi
        echo "[RMIFO]  tentativa $i/20 — aguardando 3s..."
        sleep 3
    done

    # Aplica schema (CREATE TABLE IF NOT EXISTS / CREATE OR REPLACE VIEW)
    echo "[RMIFO] Aplicando schema..."
    if psql "$DATABASE_URL" -v ON_ERROR_STOP=0 -f "$SCHEMA"; then
        echo "[RMIFO] Schema OK."
    else
        echo "[RMIFO] Schema retornou erros (podem ser avisos normais em re-deploy)."
    fi

    # Aplica seed apenas se a tabela cables estiver vazia
    CABLE_COUNT=$(psql "$DATABASE_URL" -t -c "SELECT COUNT(*) FROM cables;" 2>/dev/null | tr -d ' \n' || echo "0")
    if [[ "$CABLE_COUNT" == "0" || -z "$CABLE_COUNT" ]]; then
        echo "[RMIFO] Tabela cables vazia — aplicando seed ($SEED)..."
        if psql "$DATABASE_URL" -v ON_ERROR_STOP=0 -f "$SEED"; then
            echo "[RMIFO] Seed OK."
        else
            echo "[RMIFO] Seed retornou erros."
        fi
    else
        echo "[RMIFO] Tabela cables já tem $CABLE_COUNT registros — seed ignorado."
    fi
fi

echo "[RMIFO] Iniciando PHP server na porta ${PORT:-8080}..."
exec php -S "0.0.0.0:${PORT:-8080}" -t "$SCRIPT_DIR/web/"
