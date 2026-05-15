# RMIFO — Resilience Map of International Fiber Optics

Plataforma de análise e monitoramento de cabos submarinos de fibra ótica com três interfaces: aplicação web PHP, terminal interativo em C e dashboard gráfico Python.

---

## Estrutura do projeto

```
RMIFO/
├── README.md
├── .gitignore
├── data/                              ← datasets e logs de runtime (gitignored)
│   ├── external/                      ← CSVs e JSONs de cabos e GeoIP
│   └── local/log/                     ← histórico de sondas e telemetria
│
└── global-network-resilience/
    ├── web/                           ← Interface web (PHP + HTML + JS)
    │   ├── index.html
    │   ├── api.php
    │   └── assets/
    │       ├── css/app.css
    │       └── js/app.js
    │
    ├── terminal/                      ← Aplicação CLI em C
    │   ├── Makefile
    │   └── src/
    │       ├── rmifo.c                ← entry point e menu principal
    │       ├── db.c / db.h            ← conexão libpq
    │       ├── display.c / display.h  ← ANSI, tabelas, sparklines
    │       ├── search.c / search.h    ← 9 modos de busca + busca binária
    │       ├── routes.c / routes.h    ← DFS rotas, BFS cortes críticos
    │       ├── telemetry.c / .h       ← sparklines de telemetria e alertas
    │       └── probe.c / probe.h      ← TCP/ICMP/HTTP/UDP-DNS + JSONL
    │
    ├── dashboard/                     ← Dashboard gráfico Python
    │   ├── dashboard.py
    │   ├── requirements.txt
    │   └── build.sh                   ← PyInstaller cross-platform
    │
    ├── db/                            ← Banco de dados
    │   ├── schema.sql                 ← schema PostgreSQL
    │   ├── seed.sql                   ← 15 cabos reais + telemetria + alertas
    │   └── setup.sh                   ← setup completo em um comando
    │
    └── scripts/                       ← Scripts operacionais
        ├── install_postgres_ubuntu.sh
        ├── start_local_postgres.sh
        ├── start_web.sh
        ├── bootstrap_test_db.sh
        ├── import_catalog_data.py
        ├── ingest_telemetry_samples.py
        ├── compute_telemetry_rollups.py
        ├── validate_telemetry_baseline.py
        ├── export_telemetry_csv.py
        └── run_daily_telemetry_tasks.sh
```

---

## Funcionalidades

### Interface Web — `web/`
- Busca por cable_id com demonstração visual de busca binária
- Filtros por nome, owners, ano, status operacional, endpoints e país
- Detalhes do cabo com metadata, endpoints e amostras de IP
- Sonda de rede multi-protocolo: TCP → ICMP → HTTP → UDP/DNS
- Modo sonda completa: 5 portas, 5 amostras, jitter e throughput
- Histórico de sondas em `data/local/log/probe/YYYY-MM-DD.jsonl`
- Telemetria com sparklines e estatísticas p50/p95/p99
- Alertas ativos de telemetria

### Terminal C — `terminal/`
- Menu principal com 6 módulos: Busca · Detalhes · Rotas · Sonda · Telemetria · Alertas
- Busca binária animada passo a passo com cores ANSI
- 9 modos de busca: nome, proprietário, ano, status, landing point, supplier, endpoint, país, cable_id
- Análise de rotas via DFS (top 3) e cortes críticos via BFS
- Sonda multi-protocolo com fallback sintético e histórico JSONL
- Sparklines ▁▂▃▄▅▆▇█ de latência, jitter, perda, throughput e disponibilidade

### Dashboard Python — `dashboard/`
- Janela gráfica nativa (Tkinter + matplotlib), funciona no VS Code e standalone
- 5 painéis: Busca · Telemetria · Sonda · Rotas · Alertas
- Gráficos ao vivo com auto-refresh a cada 30 segundos
- Grafo interativo de países/cabos via NetworkX
- Modo demo automático sem banco de dados
- Executável cross-platform via PyInstaller

### Banco de Dados — `db/`
- 699 cabos reais, 1.934 landing points, 7.077 amostras de IP sondáveis
- 8.583 amostras horárias de telemetria (30 dias) + 7.210 rollups
- Vista `endpoints` para compatibilidade entre as interfaces
- 4 alertas de teste ativos

---

## Stack

| Camada | Tecnologia |
|---|---|
| Frontend web | HTML5, CSS3, JavaScript (Vanilla) |
| Backend web | PHP 8.4 com PDO PostgreSQL |
| Terminal | C11, libpq, GCC, POSIX |
| Dashboard | Python 3.12 · Tkinter · Matplotlib · NetworkX · psycopg2 |
| Banco | PostgreSQL 16 local (socket Unix, porta 55432) |

---

## Setup rápido

```bash
# 1. PostgreSQL local (apenas na primeira vez)
cd global-network-resilience
./scripts/install_postgres_ubuntu.sh

# 2. Banco com schema + seed de dados reais
bash db/setup.sh

# 3. Escolha a interface
```

---

## Comandos por interface

### Web
```bash
cd global-network-resilience
./scripts/start_web.sh
# Acesse: http://localhost:8080/
```

### Terminal C
```bash
cd global-network-resilience/terminal
make
./rmifo
```

### Dashboard Python
```bash
pip install -r global-network-resilience/dashboard/requirements.txt
python global-network-resilience/dashboard/dashboard.py
```

### Executável standalone (cross-platform)
```bash
cd global-network-resilience/dashboard
bash build.sh          # auto-detecta Linux / Windows / macOS
# Executável em: dist/rmifo-dashboard
```

---

## Banco de dados

```bash
# Setup completo (recomendado)
cd global-network-resilience
bash db/setup.sh           # cria, aplica schema e seed
bash db/setup.sh --reset   # apaga e recria do zero

# Conectar via psql
/workspaces/RMIFO/.local/postgresql/usr/lib/postgresql/16/bin/psql \
  -h /workspaces/RMIFO/.local/run -p 55432 -U postgres \
  -d global_network_resilience
```

### Variáveis de ambiente (todas as interfaces)
```bash
export WORKSPACE_ROOT=/workspaces/RMIFO
export PG_SOCKET=/workspaces/RMIFO/.local/run
export PG_PORT=55432
export PGDATABASE=global_network_resilience
export PGUSER=postgres
```

---

## Pipeline de telemetria

```bash
cd global-network-resilience
./scripts/run_daily_telemetry_tasks.sh   # execução manual

# Cron diário às 03:15 UTC
(crontab -l 2>/dev/null; echo "15 3 * * * cd /workspaces/RMIFO/global-network-resilience && ./scripts/run_daily_telemetry_tasks.sh >> /workspaces/RMIFO/data/local/log/telemetry_cron.log 2>&1") | crontab -
```

---

## Regras de organização

| Tipo | Local |
|---|---|
| Interface web (HTML, PHP, JS, CSS) | `global-network-resilience/web/` |
| Fontes C e headers | `global-network-resilience/terminal/src/` |
| Dashboard Python | `global-network-resilience/dashboard/` |
| Schema e seed SQL | `global-network-resilience/db/` |
| Scripts operacionais | `global-network-resilience/scripts/` |
| Datasets externos | `data/external/` (gitignored) |
| Logs e runtime | `data/local/` (gitignored) |
| PostgreSQL local | `.local/` (gitignored) |
| Documentação | `README.md` (raiz do workspace) |
