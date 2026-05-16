# RMIFO — Resilience Map of International Fiber Optics

Plataforma de análise e monitoramento de cabos submarinos de fibra ótica com três interfaces: aplicação web PHP, terminal interativo em C e dashboard gráfico Python.

---

## Estrutura do projeto

```
RMIFO/
├── README.md
├── data/
│   ├── external/                      ← CSVs e JSONs de cabos e GeoIP
│   └── local/
│       ├── history/                   ← Histórico de probes TCP reais (JSON)
│       └── log/                       ← Logs de runtime
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
    │       ├── rmifo.c
    │       ├── db.c / db.h
    │       ├── display.c / display.h
    │       ├── search.c / search.h
    │       ├── routes.c / routes.h
    │       ├── telemetry.c / .h
    │       └── probe.c / probe.h
    │
    ├── dashboard/                     ← Dashboard gráfico Python
    │   ├── dashboard.py
    │   ├── requirements.txt
    │   └── build.sh
    │
    ├── db/
    │   ├── schema.sql
    │   ├── seed.sql
    │   └── setup.sh
    │
    └── scripts/
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

#### Mapa Global
- Globo 3D interativo com todos os 699 cabos submarinos e seus landing points
- Cores por status: verde (operacional) · amarelo (alerta) · roxo (TCP probe real) · vermelho (inativo)
- Painel lateral ao clicar em endpoint: metadata, botão "Sondar" e "Ver Detalhes"
- Legenda com indicador de cables com dados TCP reais de probe

#### Busca de Cabos
- 9 modos de busca: ID, Nome, Proprietário, Fornecedor, Ano, Status, Landing Point, Endpoint, País
- Visualização animada de busca binária (modo ID): passos anterior/próximo/reproduzir/reiniciar
- Tabela de resultados filtra apenas cabos com dados semelhantes ao termo buscado
- Destaque visual das etapas da busca binária no ID exato no array completo

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
- Calcula grau de conectividade (cabos), alcance de países e score de risco
- Classifica congestionamento: alto · médio · baixo

#### Cortes Críticos
- Selecione par de países para análise de isolamento
- Identifica quais cabos, se cortados, isolam completamente a conectividade entre os países
- Ranking por criticidade e número de rotas afetadas

#### Sonda & Telemetria (página unificada)
- **Input de cabo**: campo de texto com autocomplete por nome ou ID
- **Seletor de modo**:
  - **Histórico de Janela** — selecione janela de 6 h / 24 h / 3 d / 7 d / 30 d
  - **Monitoramento em Tempo Real** — dashboard ao vivo com 60 segundos de histórico
- **Barra de legenda**: Janela · Qualidade · Origem · Amostras — atualiza com dados reais quando disponíveis
- **Dados Reais (TCP Probe)**: lê arquivos `data/local/history/` e exibe:
  - Média de latência, jitter, perda de pacotes, throughput, disponibilidade, qualidade
  - Atualiza automaticamente Janela/Qualidade/Origem/Amostras com valores reais
  - Número exato de amostras TCP coletadas
- **Gráfico com overlay**: série DB/sintética (linha tracejada roxa) + pontos reais TCP (linha sólida verde)
- **Modo tempo real**: 4 gráficos 2D atualizando a cada 500 ms — Latência (EMA), Jitter, Perda, Interferência
- **Alertas**: badge no header com contagem de alertas ativos; ticker de alertas na base da tela

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

#### Painel: Busca de Cabos
- 9 modos de busca: nome, cabo_id, proprietário, fornecedor, ano, status, landing, endpoint, país
- Tabela com coluna **TCP** indicando cabos com dados de probe real (`data/local/history/`)
- Cabos com probe TCP real destacados em roxo na tabela
- Duplo clique abre janela de detalhes: metadata + endpoints + IP sample

#### Painel: Sonda & Telemetria (unificado)
- Campo de texto para selecionar cabo por nome ou ID
- Toggle de modo: **Histórico de Janela** | **Monitoramento em Tempo Real**
- Barra de legenda: Janela · Qualidade · Origem · Amostras
- **Dados Reais**: lê `data/local/history/*_probe_full_*.json` e `*_checkpoint_*.json`
  - Calcula médias reais: latência, jitter, perda, throughput, disponibilidade, qualidade
  - Cards visuais com valores médios por tipo de métrica (coloridos por tipo)
- **Gráficos 2×2**: Latência · Jitter · Perda · Throughput — sobreposição de série DB (tracejada) + TCP real (sólida verde)
- **Modo tempo real**: 4 gráficos atualizando a cada 500 ms com EMA na latência; métricas ao vivo em destaque

#### Painel: Top 3 Rotas
- Input de país de origem e destino
- Busca cabos operacionais com landing points nos dois países
- Exibe top 3 rotas com cable_id e nome

#### Painel: Risco de Endpoints
- Input de país
- Calcula grau de conectividade, alcance e score de risco por endpoint
- Tabela com classificação: alto (vermelho) · médio (amarelo) · baixo (verde)

#### Painel: Cortes Críticos
- Input de par de países
- Identifica cabos críticos cuja remoção isolaria os países
- Marcação visual de criticidade na tabela de saída

#### Painel: Alertas
- Lista de alertas ativos de telemetria com severidade, valor atual vs baseline e timestamp
- Badge de alertas no cabeçalho da janela

---

### Banco de Dados — `db/`
- 699 cabos reais, 1.934 landing points, 7.077 amostras de IP sondáveis
- Tabelas: `cable_telemetry_samples`, `telemetry_rollup_5m`, `telemetry_rollup_1h`
- Vista `endpoints` para compatibilidade entre as interfaces
- Tabela `telemetry_alerts` com alertas ativos e histórico

---

## Stack

| Camada | Tecnologia |
|---|---|
| Frontend web | HTML5 + CSS3 + JavaScript (Vanilla) + globe.gl + Plotly.js |
| Backend web | PHP 8.3 com PDO PostgreSQL |
| Terminal | C11 · libpq · GCC · POSIX |
| Dashboard | Python 3.12 · Tkinter · Matplotlib · psycopg2 |
| Banco | PostgreSQL 16 local (socket Unix, porta 55432) |
| Histórico TCP | JSON em `data/local/history/` (probe_full + checkpoint) |

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
cd global-network-resilience/terminal
make
./rmifo
```

### Dashboard Python
```bash
pip install -r global-network-resilience/dashboard/requirements.txt
WORKSPACE_ROOT=/workspaces/RMIFO python global-network-resilience/dashboard/dashboard.py
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

### Variáveis de ambiente
```bash
export WORKSPACE_ROOT=/workspaces/RMIFO
export PG_SOCKET=/workspaces/RMIFO/.local/run
export PG_PORT=55432
export PGDATABASE=global_network_resilience
export PGUSER=postgres
```

---

## Pipeline de telemetria

Os scripts em `scripts/` produzem e consomem dados em dois destinos:

| Destino | Formato | Conteúdo |
|---|---|---|
| `data/local/history/` | JSON por execução | Registros brutos TCP por cabo (`probe_full`, `checkpoint`) |
| PostgreSQL | Tabelas relacionais | Rollups 5 m e 1 h, alertas, amostras de referência |

```bash
# Execução manual do pipeline completo
cd global-network-resilience
./scripts/run_daily_telemetry_tasks.sh

# Ingestão de amostras
python scripts/ingest_telemetry_samples.py

# Rollups
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

## Arquivos de histórico TCP (`data/local/history/`)

Gerados pelos scripts de sonda e ingestão, consumidos pela interface web e pelo dashboard para exibir **médias de dados reais**:

| Padrão de nome | Conteúdo |
|---|---|
| `YYYY-MM-DD_probe_full_HHMMSS.json` | Probe completo de todos os 699 cabos |
| `YYYY-MM-DD_checkpoint_NNNN_HHMMSS.json` | Checkpoint incremental (50 cabos) |
| `YYYY-MM-DD_rollup_HHMMSS.json` | Resumo agregado por cabo |

Campos por registro:
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

---

## Regras de organização

| Tipo | Local |
|---|---|
| Interface web (HTML, PHP, JS, CSS) | `global-network-resilience/web/` |
| Fontes C e headers | `global-network-resilience/terminal/` |
| Dashboard Python | `global-network-resilience/dashboard/` |
| Schema e seed SQL | `global-network-resilience/db/` |
| Scripts operacionais | `global-network-resilience/scripts/` |
| Datasets externos | `data/external/` (gitignored) |
| Histórico TCP real | `data/local/history/` (gitignored) |
| Logs e runtime | `data/local/log/` (gitignored) |
| PostgreSQL local | `.local/` (gitignored) |
