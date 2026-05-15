# RMIFO — Resilience Map of International Fiber Optics

Plataforma de análise e monitoramento de cabos submarinos de fibra ótica com três interfaces complementares: aplicação web PHP, terminal interativo em C e dashboard gráfico Python.

---

## O que está pronto

### Interface Web — `Mods/SITE/`
- Busca por cable_id com demonstração visual de busca binária.
- Filtros por nome, owners, ano, status operacional, endpoints e país.
- Aba de detalhes com metadata do cabo, endpoints e amostras de IP público.
- Sonda de rede multi-protocolo com cadeia TCP → ICMP → HTTP → UDP/DNS.
- Modo sonda completa com 5 amostras, jitter e throughput.
- Histórico de sondas salvo em `data/local/log/probe/YYYY-MM-DD.jsonl`.
- Telemetria avançada com sparklines e análise estatística (p50/p95/p99).
- Visualização de alertas ativos de telemetria.

### Terminal Interativo em C — `Mods/TERMINAL/rmifo`
- Menu principal com 6 módulos: Busca, Detalhes, Rotas, Sonda, Telemetria, Alertas.
- Busca binária animada passo a passo com cores ANSI.
- 9 modos de busca: nome, proprietário, ano, status, landing point, supplier, endpoint, país, cable_id.
- Análise de rotas via DFS (top 3 caminhos) e cortes críticos via BFS.
- Sonda multi-protocolo: TCP → ICMP → HTTP → UDP/DNS com fallback sintético.
- Sparklines de telemetria ▁▂▃▄▅▆▇█: latência, jitter, perda, throughput, disponibilidade.
- Histórico de sondas salvo em JSONL compatível com o banco.
- Compilado com `make` em `Mods/TERMINAL/`.

### Dashboard Gráfico Python — `Mods/TERMINAL/dashboard.py`
- Interface Tkinter com matplotlib embutido; abre janela gráfica nativa no VS Code e em qualquer SO.
- 5 painéis: Busca · Telemetria · Sonda · Rotas · Alertas.
- Telemetria com 5 gráficos ao vivo e auto-refresh a cada 30 segundos.
- Sonda multi-protocolo com barra de progresso e badges coloridos por protocolo.
- Grafo interativo de países/cabos via NetworkX.
- Modo demo automático quando o banco não está disponível.
- Executável cross-platform via PyInstaller (`bash build.sh`).

### Banco de Dados — `db/`
- Setup em um comando: `bash db/setup.sh`.
- 699 cabos reais catalogados, 1.934 landing points, 7.077 amostras de IP sondáveis.
- 8.583 amostras horárias de telemetria (30 dias sintéticos) + 7.210 rollups calculados.
- Vista `endpoints` para compatibilidade entre as três interfaces.
- 4 alertas de teste ativos (2 critical, 2 warning).

---

## Stack

| Camada | Tecnologia |
|---|---|
| Frontend web | HTML5, CSS3, JavaScript (Vanilla) |
| Backend web | PHP 8.4 com PDO PostgreSQL |
| Terminal | C11, libpq, GCC, POSIX |
| Dashboard | Python 3.12 · Tkinter · Matplotlib · NetworkX · psycopg2 |
| Banco | PostgreSQL 16 local (socket Unix, porta 55432) |
| Importação | Python com psycopg2 |

---

## Estrutura de arquivos

```
global-network-resilience/
│
├── Mods/
│   ├── SITE/                          ← Interface web completa
│   │   ├── index.html                 ← SPA principal
│   │   ├── api.php                    ← Backend PHP (busca, probe, telemetria)
│   │   └── assets/
│   │       ├── css/app.css
│   │       └── js/app.js
│   │
│   ├── TERMINAL/                      ← Terminal C + Dashboard Python
│   │   ├── Makefile                   ← Build do terminal C
│   │   ├── rmifo.c                    ← Entry point (banner, menu principal)
│   │   ├── db.c / db.h                ← Conexão libpq via socket/porta
│   │   ├── display.c / display.h      ← ANSI, tabelas box-drawing, sparklines
│   │   ├── search.c / search.h        ← 9 modos de busca + busca binária visual
│   │   ├── routes.c / routes.h        ← DFS top-3 rotas, BFS cortes críticos
│   │   ├── telemetry.c / telemetry.h  ← Sparklines de telemetria, alertas
│   │   ├── probe.c / probe.h          ← TCP/ICMP/HTTP/UDP-DNS + histórico JSONL
│   │   ├── rmifo                      ← Binário compilado
│   │   ├── dashboard.py               ← Dashboard gráfico Python/Tkinter
│   │   ├── requirements.txt           ← Dependências Python
│   │   └── build.sh                   ← PyInstaller cross-platform
│   │
│   └── teste.SQL                      ← Schema PostgreSQL principal
│
├── db/
│   ├── setup.sh                       ← Setup completo em um comando
│   └── seed.sql                       ← 15 cabos reais + telemetria + alertas
│
├── scripts/
│   ├── install_postgres_ubuntu.sh     ← Instala PostgreSQL localmente
│   ├── start_local_postgres.sh        ← Inicia servidor PostgreSQL
│   ├── bootstrap_test_db.sh           ← Bootstrap com catálogo externo CSV/JSON
│   ├── import_catalog_data.py         ← Importa CSV/JSON do catálogo externo
│   ├── ingest_telemetry_samples.py    ← Coleta e grava amostras históricas
│   ├── validate_telemetry_baseline.py ← Compara janela recente vs baseline
│   ├── compute_telemetry_rollups.py   ← Calcula rollups 5m e 1h
│   ├── export_telemetry_csv.py        ← Exporta telemetria para CSV
│   ├── run_daily_telemetry_tasks.sh   ← Pipeline diário (ingestão + validação)
│   └── start_web.sh                   ← Sobe servidor PHP embutido
│
└── data/                              ← PROTEGIDA — não apagar sem confirmação
    └── external/                      ← Datasets externos (CSV, JSON, GeoIP)
```

---

## Módulos do Terminal C

| Arquivo | Responsabilidade |
|---|---|
| `rmifo.c` | Entry point, banner ASCII, menu principal |
| `db.c` | Conexão libpq via socket Unix, lê variáveis de ambiente |
| `display.c` | Cores ANSI, tabelas box-drawing, `press_enter`, `read_choice` |
| `search.c` | Cache de cabos, busca binária animada, 9 modos de busca |
| `routes.c` | Grafo dinâmico, DFS top-3 rotas, BFS cortes críticos, risco de endpoint |
| `telemetry.c` | Sparklines ▁▂▃▄▅▆▇█, telemetria por cabo, alertas ativos |
| `probe.c` | TCP não-bloqueante, ICMP via popen, HTTP HEAD, UDP/DNS, JSONL |

## Painéis do Dashboard Python

| Painel | Funcionalidade |
|---|---|
| Busca | Pesquisa por nome/país/proprietário/ano; duplo-clique abre detalhe do cabo |
| Telemetria | 5 gráficos matplotlib: lat · jitter · perda · throughput · disponib.; auto-refresh 30s |
| Sonda | Cadeia TCP→ICMP→HTTP→UDP/DNS; barra de progresso; gráfico de RTT por amostra |
| Rotas | Busca origem→destino; grafo NetworkX de países/cabos |
| Alertas | Tabela colorida: vermelho=critical, amarelo=warning |

---

## Banco de dados — tabelas principais

| Tabela | Registros | Conteúdo |
|---|---|---|
| `cables` | 699 | cable_id, cable_name, length_km, is_operational, owners, capacity_gbps |
| `cable_metadata` | 699 | URLs, notas, is_planned, raw_length |
| `landing_points` | 1.934 | Pontos de aterrissagem com lat/lon, country, city |
| `cable_landing_points` | 3.206 | Ligações cabo↔endpoint com ordem |
| `endpoint_ip_samples` | 7.077 | Blocos IP por endpoint (ip_start, ip_end, CIDR) |
| `cable_telemetry_samples` | 8.583+ | Amostras horárias: latência, jitter, perda, throughput |
| `telemetry_rollup_1h` | 7.210 | Rollups calculados: avg, max, p95 |
| `telemetry_alerts` | variável | Alertas ativos com severidade vs baseline |
| `cable_telemetry_reference` | variável | Baselines por cabo para comparação |
| `endpoints` (VIEW) | — | Une landing_points + cable_landing_points + ip_samples |

---

## Setup rápido (primeira vez)

```bash
# 1. PostgreSQL local — só na primeira vez
cd /workspaces/RMIFO/global-network-resilience
./scripts/install_postgres_ubuntu.sh

# 2. Banco com schema + dados de teste reais
bash db/setup.sh

# 3. Escolha a interface (ver seções abaixo)
```

---

## Interface Web

```bash
cd /workspaces/RMIFO/global-network-resilience
./scripts/start_web.sh
# Acesse: http://localhost:8080/
```

---

## Terminal C

```bash
# Compilar (só uma vez)
cd /workspaces/RMIFO/global-network-resilience/Mods/TERMINAL
make

# Executar
./rmifo
```

Variáveis de ambiente reconhecidas pelo terminal:

```bash
export WORKSPACE_ROOT=/workspaces/RMIFO
export PG_SOCKET=/workspaces/RMIFO/.local/run
export PG_PORT=55432
```

---

## Dashboard Python

```bash
# Instalar dependências (só uma vez)
pip install -r /workspaces/RMIFO/global-network-resilience/Mods/TERMINAL/requirements.txt

# Executar — abre janela gráfica externa
python /workspaces/RMIFO/global-network-resilience/Mods/TERMINAL/dashboard.py
```

Se o gráfico não abrir em janela separada no VS Code, adicione no início do script:

```python
import matplotlib
matplotlib.use('TkAgg')  # ou 'Qt5Agg' dependendo do sistema
```

### Gerar executável standalone

```bash
cd /workspaces/RMIFO/global-network-resilience/Mods/TERMINAL
bash build.sh            # auto-detecta Linux / Windows / macOS
bash build.sh windows    # forçar Windows
bash build.sh macos      # forçar macOS
# Executável em: dist/rmifo-dashboard  (ou .exe no Windows)
```

---

## Banco de dados

### Setup recomendado

```bash
cd /workspaces/RMIFO/global-network-resilience
bash db/setup.sh           # cria banco, schema, seed e view de compatibilidade
bash db/setup.sh --reset   # apaga e recria do zero
```

### Bootstrap alternativo (com catálogo externo)

```bash
# Requer: data/external/submarine_cables.csv
#         data/external/submarine_cables_full.json
#         data/external/geoip_database.csv
cd /workspaces/RMIFO/global-network-resilience
./scripts/bootstrap_test_db.sh
```

### Conectar via psql

```bash
/workspaces/RMIFO/.local/postgresql/usr/lib/postgresql/16/bin/psql \
  -h /workspaces/RMIFO/.local/run \
  -p 55432 -U postgres -d global_network_resilience
```

### Variáveis de ambiente (para as três apps)

```bash
export WORKSPACE_ROOT=/workspaces/RMIFO
export PG_SOCKET=/workspaces/RMIFO/.local/run
export PG_PORT=55432
export PGDATABASE=global_network_resilience
export PGUSER=postgres
```

---

## Pipeline diário de telemetria

```bash
# Execução manual
cd /workspaces/RMIFO/global-network-resilience
./scripts/run_daily_telemetry_tasks.sh

# Cron diário às 03:15 UTC
chmod +x scripts/run_daily_telemetry_tasks.sh
(crontab -l 2>/dev/null; echo "15 3 * * * cd /workspaces/RMIFO/global-network-resilience && ./scripts/run_daily_telemetry_tasks.sh >> results/telemetry_cron.log 2>&1") | crontab -
```

Scripts do pipeline:
- `scripts/ingest_telemetry_samples.py` — coleta e grava amostras com metadados de confiança.
- `scripts/validate_telemetry_baseline.py` — compara janela recente vs baseline; gera relatórios JSON/Markdown.
- `scripts/compute_telemetry_rollups.py` — calcula rollups de 5m e 1h.
- `scripts/run_daily_telemetry_tasks.sh` — orquestra ingestão + validação + rollups.

---

## Regras de organização

- Arquivos `.md` e `.txt` de topo ficam em `/Notebooks`.
- `global-network-resilience/data/` é **protegida** — qualquer remoção exige confirmação explícita.
- `global-network-resilience/.local/` é exclusiva de runtime — não entra no Git.
- Site (HTML, PHP, JS, CSS) → `Mods/SITE/`.
- Terminal C e dashboard Python → `Mods/TERMINAL/`.
- Setup e seed do banco → `db/`.
- Não remover datasets em `data/external/` sem validação prévia.
