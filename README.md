# RMIFO — Resilience Map of International Fiber Optics

Plataforma de análise e monitoramento de cabos submarinos de fibra ótica com três interfaces: aplicação web PHP (produção no Railway), terminal interativo em C e dashboard gráfico Python.

---

## Stack

| Camada | Tecnologia |
|---|---|
| Frontend web | HTML5 · CSS3 · JavaScript Vanilla · MapLibre GL JS 4.7.1 · Plotly.js 2.35.2 |
| Backend web | PHP 8.2 · PDO PostgreSQL · pdo_pgsql |
| Deploy | Railway (Nixpacks) · Node.js 20 · pg 8.11 |
| Terminal | C11 · libpq · GCC · POSIX |
| Dashboard | Python 3.12 · Tkinter · Matplotlib · psycopg2 |
| Banco | PostgreSQL 16 (Railway gerenciado / socket local porta 55432) |
| PWA | Service Worker (rmifo-v7) · Web App Manifest |
| Pipeline | Python 3.12 · psycopg2-binary |

---

## Bibliotecas

### Interface Web
| Biblioteca | Versão | Uso |
|---|---|---|
| [MapLibre GL JS](https://maplibre.org/maplibre-gl-js/) | 4.7.1 | Globo 3D interativo com projeção `globe`, camadas GeoJSON para cabos e pontos |
| [Plotly.js](https://plotly.com/javascript/) | 2.35.2 | Gráficos em tempo real (latência, jitter, perda, interferência) — carregado sob demanda |
| [demotiles.maplibre.org](https://demotiles.maplibre.org) | — | Tiles vetoriais de base (países, água, estados) com tema escuro |

### Backend / Deploy
| Biblioteca | Versão | Uso |
|---|---|---|
| `pg` (Node.js) | ^8.11 | Aplicação de migrations do Railway na inicialização |
| `pdo_pgsql` (PHP) | — | Conexão API → PostgreSQL via PDO |

### Pipeline de Telemetria (Python)
| Biblioteca | Versão | Uso |
|---|---|---|
| `psycopg2-binary` | >=2.9 | Conexão Python → PostgreSQL |

### Dashboard Python
| Biblioteca | Versão | Uso |
|---|---|---|
| `matplotlib` | 3.10.9 | Gráficos 2×2 (latência, jitter, perda, throughput) |
| `numpy` | 2.4.4 | Cálculos de médias e EMA |
| `psycopg2-binary` | 2.9.12 | Conexão com PostgreSQL |
| `networkx` | 3.6.1 | Grafo de rotas e cortes críticos |
| `pyinstaller` | 6.20.0 | Build de executável standalone |
| Tkinter | stdlib | Interface gráfica nativa |

### Terminal C
| Biblioteca | Uso |
|---|---|
| `libpq` | Conexão C → PostgreSQL |
| POSIX sockets | Sonda TCP multi-protocolo |
| ANSI escape codes | Cores e animação no terminal |

---

## Estrutura do projeto

```
RMIFO/
├── README.md
├── terminal/                          ← Aplicação CLI em C
│   ├── Makefile
│   └── src/
│       ├── rmifo.c
│       ├── db.c / db.h
│       ├── display.c / display.h
│       ├── search.c / search.h
│       ├── routes.c / routes.h
│       ├── telemetry.c / telemetry.h
│       └── probe.c / probe.h
│
├── dashboard/                         ← Dashboard gráfico Python
│   ├── dashboard.py
│   ├── requirements.txt
│   └── build.sh
│
├── data/
│   ├── external/                      ← CSVs e JSONs de cabos e GeoIP
│   └── local/
│       ├── history/                   ← Histórico de probes TCP reais (JSON)
│       └── log/                       ← Logs de runtime
│
└── global-network-resilience/         ← Raiz do deploy Railway
    ├── nixpacks.toml
    ├── railway.json
    ├── package.json
    ├── start.sh
    ├── web/                           ← Interface web (PHP + HTML + JS)
    │   ├── index.html
    │   ├── api.php
    │   ├── sw.js                      ← Service Worker PWA (rmifo-v7)
    │   ├── manifest.json
    │   └── assets/
    │       ├── css/app.css
    │       ├── js/app.js
    │       ├── icons/                 ← Ícones PWA (SVG, 192px, 512px, maskable)
    │       └── svg/                   ← SVG inline (clock, check, x-mark, alert, play, dot)
    │
    ├── db/
    │   ├── schema.sql
    │   ├── seed.sql
    │   └── setup.sh
    │
    └── scripts/
        ├── sonda.js
        ├── import_catalog_data.py
        ├── ingest_telemetry_samples.py
        ├── compute_telemetry_rollups.py
        ├── validate_telemetry_baseline.py
        ├── export_telemetry_csv.py
        ├── run_daily_telemetry_tasks.sh
        ├── pipeline.sh
        └── requirements.txt
```

---

## Funcionalidades

### Interface Web — `web/`

#### Homepage
- Exibida sempre ao carregar o site (sem gate de sessão)
- Cards de estatísticas ao vivo: total de cabos, cabos operacionais, amostras de telemetria
- Fluxo de arquitetura em 4 etapas com explicação de uso real de cada componente
- Schema ERD inline (12 tabelas) com explicação das relações
- Data da última medição registrada no banco

#### Mapa Global (Globo 3D)
- Globo 3D interativo via **MapLibre GL JS 4.7.1** com projeção `globe` — renderização WebGL em batch
- Tema escuro: oceanos `#0d0920`, continentes `#120d22`, bordas violeta `#433169`
- Atmosfera roxa via `setFog()` com `space-color` escuro e `star-intensity` visível
- Cabos renderizados como `LineString` GeoJSON — cor por status:
  - Verde `#2ddc82` — operacional
  - Roxo `#a871ff` — com dados de TCP probe real
  - Âmbar `#ffb92d` — com alerta ativo
  - Vermelho `#ff465f` — inativo
  - Amarelo `#fff03c` — cabo ativo na sonda ao vivo
- Pontos de desembarque renderizados como `Point` GeoJSON (círculos coloridos por status)
- Labels de cidade com filtro por zoom: tier 1 (capitais) → tier 2 → tier 3 conforme aproximação
- Hover tip com nome do cabo e coordenadas (throttled via `requestAnimationFrame`)
- Painel lateral ao clicar em cabo ou ponto: metadata, botão "Sondar" e "Ver Detalhes"
- Globo estático (sem rotação automática) para máxima fluidez no zoom

#### Busca de Cabos
- 9 modos: ID, Nome, Proprietário, Fornecedor, Ano, Status, Landing Point, Endpoint, País
- Visualização animada de busca binária (modo ID): passos anterior/próximo/reproduzir/reiniciar
- Tabela de resultados com destaque visual das etapas

#### Detalhes do Cabo
- Metadata completa: nome, comprimento, capacidade, proprietários, fornecedores, ano, tipo
- Lista de endpoints com país, cidade e IP sondável
- Navegação via datalist com autocomplete

#### Top 3 Rotas
- Selecione país de origem e destino
- Calcula as 3 melhores rotas via grafo de cabos operacionais
- Exibe sequência de cabos, score de resiliência e hop count

#### Risco de Endpoints
- Selecione um país para mapear endpoints de risco
- Calcula grau de conectividade, alcance e score de risco
- Classifica congestionamento: alto · médio · baixo

#### Cortes Críticos
- Selecione par de países para análise de isolamento
- Identifica quais cabos, se cortados, isolam completamente a conectividade
- Ranking por criticidade e número de rotas afetadas

#### Sonda & Telemetria
- Campo de texto com autocomplete por nome ou ID
- **Histórico de Janela** — 6 h / 24 h / 3 d / 7 d / 30 d
- **Monitoramento em Tempo Real** — 4 gráficos Plotly.js atualizando a cada 500 ms (latência EMA, jitter, perda, interferência)
- Barra de legenda: Janela · Qualidade · Origem · Amostras
- Alertas: badge no header com contagem; ticker de alertas na base

#### Sonda Diária
- Varredura progressiva de todos os cabos com pre-flight (DB, endpoints, geodata, baseline)
- Animação dupla-trilha SEND → ACK em tempo real
- Nome do cabo exibido ao vivo via `api.php?action=daily_status`
- Chip de protocolo colorido: TCP/TLS · HTTP · MTR/UDP · Sintético
- Tags de porta flutuando sobre os pacotes (`:443 TLS`, `:80 HTTP`)
- Barra de progresso global com `%` e número do cabo atual

#### Histórico
- Selecione data e cabo para visualizar métricas históricas
- Lê `cables_live.jsonl` do diretório de histórico da data selecionada

#### PWA
- Instalável via Chrome/Edge — ícone globo SVG com nós roxos
- Service Worker `rmifo-v7`: cache-first para estáticos, network-first para `/api.php`
- Shortcuts: Sonda Diária e Histórico

---

### Terminal C — `terminal/`
- Menu principal com 6 módulos: Busca · Detalhes · Rotas · Sonda · Telemetria · Alertas
- Busca binária animada passo a passo com cores ANSI
- 9 modos de busca: nome, proprietário, ano, status, landing point, supplier, endpoint, país, cable_id
- Análise de rotas via DFS (top 3) e cortes críticos via BFS
- Sonda multi-protocolo com fallback sintético e histórico JSONL
- Sparklines ▁▂▃▄▅▆▇█ de latência, jitter, perda, throughput e disponibilidade

---

### Dashboard Python — `dashboard/`

Janela gráfica nativa (Tkinter + matplotlib), funciona no VS Code e standalone.

#### Painéis disponíveis
- **Busca de Cabos** — 9 modos, tabela com indicador TCP, duplo clique abre detalhes
- **Sonda & Telemetria** — histórico de janela + tempo real com gráficos 2×2
- **Top 3 Rotas** — grafo via NetworkX, exibe sequência de cabos por par de países
- **Risco de Endpoints** — conectividade, alcance e score por país
- **Cortes Críticos** — isolamento entre países, ranking por criticidade
- **Alertas** — lista ativa com severidade, valor atual vs baseline e timestamp

---

### Banco de Dados — `db/`
- 699 cabos reais, 1.934 landing points, 7.077+ amostras de IP sondáveis
- 12 tabelas: `cables`, `cable_owners`, `cable_suppliers`, `landing_points`, `cable_endpoints`, `endpoint_ip_samples`, `cable_telemetry_samples`, `telemetry_rollup_5m`, `telemetry_rollup_1h`, `telemetry_alerts`, `telemetry_baseline`, `probe_runs`
- Vista `endpoints` para compatibilidade entre interfaces
- Endpoint `api.php?action=summary` retorna estatísticas ao vivo para a homepage

---

## Deploy Railway

```bash
# O deploy é automático via push na branch conectada ao Railway.
# O nixpacks.toml define: php82, pdo_pgsql, postgresql, nodejs_20, curl, mtr, iputils
# start.sh aplica migrations Node.js e sobe o servidor PHP
```

Variáveis de ambiente necessárias no Railway:
```
DATABASE_URL=postgresql://...   # fornecida automaticamente pelo Railway PostgreSQL
```

---

## Setup local

```bash
# 1. PostgreSQL local (apenas na primeira vez)
cd global-network-resilience
bash scripts/install_postgres_ubuntu.sh

# 2. Banco com schema + seed de dados reais
bash db/setup.sh
# ou para recriar do zero:
bash db/setup.sh --reset

# 3. Servidor web PHP
WORKSPACE_ROOT=/workspaces/RMIFO \
PG_SOCKET=/workspaces/RMIFO/.local/run \
PG_PORT=55432 \
php -d extension=/home/codespace/.php/usr/lib/php/20230831/pdo_pgsql.so \
    -S 0.0.0.0:8080 \
    -t /workspaces/RMIFO/global-network-resilience/web
# Acesse: http://localhost:8080/
```

Ou via script:
```bash
cd global-network-resilience
./scripts/start_web.sh
```

### Terminal C
```bash
cd terminal
make
./rmifo
```

### Dashboard Python
```bash
pip install -r dashboard/requirements.txt
WORKSPACE_ROOT=/workspaces/RMIFO python dashboard/dashboard.py
```

### Executável standalone
```bash
cd dashboard
bash build.sh   # auto-detecta Linux / Windows / macOS
# Executável em: dist/rmifo-dashboard
```

### Conectar ao banco local
```bash
/workspaces/RMIFO/.local/postgresql/usr/lib/postgresql/16/bin/psql \
  -h /workspaces/RMIFO/.local/run -p 55432 -U postgres \
  -d global_network_resilience
```

### Variáveis de ambiente locais
```bash
export WORKSPACE_ROOT=/workspaces/RMIFO
export PG_SOCKET=/workspaces/RMIFO/.local/run
export PG_PORT=55432
export PGDATABASE=global_network_resilience
export PGUSER=postgres
```

---

## Pipeline de telemetria

| Destino | Formato | Conteúdo |
|---|---|---|
| `data/local/history/` | JSON por execução | Registros brutos TCP por cabo (`probe_full`, `checkpoint`) |
| `data/local/historico/YYYY-MM-DD/` | `.probe_live.json` + `cables_live.jsonl` | Status em tempo real por cabo |
| PostgreSQL | Tabelas relacionais | Rollups 5 m e 1 h, alertas, amostras de referência |

```bash
# Pipeline completo
cd global-network-resilience
./scripts/run_daily_telemetry_tasks.sh

# Ingestão de amostras (grava live JSON por cabo)
python scripts/ingest_telemetry_samples.py

# Rollups 5m e 1h
python scripts/compute_telemetry_rollups.py

# Validação de baseline e geração de alertas
python scripts/validate_telemetry_baseline.py

# Cron diário às 03:15 UTC
(crontab -l 2>/dev/null; \
 echo "15 3 * * * cd /workspaces/RMIFO/global-network-resilience && \
   ./scripts/run_daily_telemetry_tasks.sh \
   >> /workspaces/RMIFO/data/local/log/telemetry_cron.log 2>&1") | crontab -
```

---

## Arquivos de histórico TCP

### Probe histórico — `data/local/history/`

| Padrão de nome | Conteúdo |
|---|---|
| `YYYY-MM-DD_probe_full_HHMMSS.json` | Probe completo de todos os cabos |
| `YYYY-MM-DD_checkpoint_NNNN_HHMMSS.json` | Checkpoint incremental (50 cabos) |
| `YYYY-MM-DD_rollup_HHMMSS.json` | Resumo agregado por cabo |

```json
{
  "cable_id": "2africa",
  "measured_at": "2026-05-16T01:26:00+00:00",
  "latency_ms": 102.2,
  "jitter_ms": 15.651,
  "packet_loss_pct": 0.293,
  "throughput_mbps": 150892703.272,
  "availability_pct": 99.949,
  "source": "active_tcp_connect",
  "confidence_score": 0.95
}
```

### Live sonda — `data/local/historico/YYYY-MM-DD/`

| Arquivo | Conteúdo |
|---|---|
| `.probe_live.json` | Último cabo: `cable_id`, `cable_name`, `current`, `total`, `percent`, `proto`, `latency_ms` |
| `cables_live.jsonl` | Uma linha JSON por cabo concluído na sessão |

---

## Regras de organização

| Tipo | Local |
|---|---|
| Interface web (HTML, PHP, JS, CSS) | `global-network-resilience/web/` |
| Ícones PWA | `global-network-resilience/web/assets/icons/` |
| SVG inline | `global-network-resilience/web/assets/svg/` |
| Fontes C e headers | `terminal/` |
| Dashboard Python | `dashboard/` |
| Schema e seed SQL | `global-network-resilience/db/` |
| Scripts operacionais | `global-network-resilience/scripts/` |
| Datasets externos | `data/external/` (gitignored) |
| Histórico TCP real | `data/local/history/` (gitignored) |
| Live sonda | `data/local/historico/` (gitignored) |
| Logs e runtime | `data/local/log/` (gitignored) |
| PostgreSQL local | `.local/` (gitignored) |
