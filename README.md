# RMIFO — Resilience Map of International Fiber Optics

Plataforma open-source de monitoramento e análise de cabos submarinos de fibra ótica. Coleta telemetria TCP/MTR/ICMP/HTTP/BGP/SNMP, persiste em PostgreSQL e exibe em globo 3D interativo via MapLibre GL JS. Implantada no Railway com suporte a PWA offline.

---

## Stack

| Camada | Tecnologia |
|---|---|
| Frontend | HTML5 · CSS3 · JavaScript (Vanilla) · MapLibre GL JS 4.7.1 · Plotly.js 2.35.2 |
| Backend | PHP 8.2 · PDO PostgreSQL (`pdo_pgsql`) |
| Banco de dados | PostgreSQL 16 — Railway gerenciado / socket local |
| Deploy | Railway · Nixpacks · Node.js 20 |
| Pipeline de sonda | Python 3.12 · Node.js · Bash |
| PWA | Service Worker (`rmifo-v18`) · Web App Manifest |

---

## Linguagens

| Linguagem |
|---|
| JavaScript |
| PHP |
| Python |
| CSS |
| HTML |
| Shell |

---

## Estrutura do projeto

```
RMIFO/
└── global-network-resilience/         ← Raiz do deploy Railway
    ├── nixpacks.toml
    ├── railway.json
    ├── package.json
    ├── start.sh
    ├── web/
    │   ├── index.html                 ← SPA com 8 páginas
    │   ├── api.php                    ← Backend REST PHP (~2 600 linhas)
    │   ├── sw.js                      ← Service Worker PWA (rmifo-v18)
    │   ├── manifest.json
    │   └── assets/
    │       ├── css/app.css
    │       ├── js/app.js
    │       └── icons/
    ├── db/
    │   ├── schema.sql                 ← 13 tabelas PostgreSQL
    │   └── migrations/
    └── scripts/
        ├── sonda.js                   ← Sonda TCP/MTR Node.js (699 cabos)
        ├── rmifo_probe_multiprotocol.py ← BGP/SNMP/NetFlow multi-protocolo
        ├── ingest_telemetry_samples.py
        ├── compute_telemetry_rollups.py
        ├── run_daily_telemetry_tasks.sh ← Orquestrador diário
        └── requirements.txt
```

---

## Banco de dados (13 tabelas)

| Tabela | Descrição |
|---|---|
| `cables` | 699 cabos catalogados com metadata completa |
| `landing_points` | ~1 900 pontos de aterrissagem geolocalizados |
| `cable_landing_points` | Relação cabo ↔ endpoint |
| `cable_telemetry_samples` | Amostras TCP/MTR/HTTP por cabo (UNIQUE cable_id + measured_at) |
| `cable_telemetry_hops` | Saltos hop-by-hop de MTR por amostra |
| `cable_telemetry_reference` | Baseline de referência por cabo |
| `hop_switch_geolocations` | Geolocalização de comutadores de rota descobertos |
| `lldp_topology_map` | Topologia LLDP/SNMP |
| `netflow_traffic_samples` | Amostras NetFlow |
| `bgp_probe_results` | Resultados de sonda BGP (RIPE STAT) |
| `snmp_probe_results` | Resultados de sonda SNMP |
| `telemetry_alerts` | Alertas ativos e histórico |
| `api_rate_limit` | Controle de rate limit por IP |

---

## Funcionalidades

### Homepage

- Contadores animados ao vivo: cabos, operacionais, amostras, landing points, países, tabelas, comutadores geolocalizados, relações cabo→LP
- Badges de linguagens com ícones DevIcons
- Fluxo "Como funciona" em 3 etapas: Sonda Diária → PostgreSQL → Globo 3D
- Status ao vivo: PostgreSQL, API PHP, alertas ativos, hops de rota
- Última medição registrada no banco

### Mapa Global

- Globo interativo **MapLibre GL JS 4.7.1** com projeção `globe` (WebGL)
- Cabos como `LineString` GeoJSON — cor por status:
  - Verde `#2ddc82` — operacional
  - Roxo `#a871ff` — com telemetria TCP verificada
  - Âmbar `#ffb92d` — com alerta ativo
  - Vermelho `#ff465f` — inativo
  - Amarelo `#fff03c` — sonda ao vivo
- Pontos de aterrissagem como círculos coloridos
- **Comutadores de rota** (hop geolocalizados) — pontos âmbar/vermelho por número de cabos associados
- **Linhas comutador → landing point** — traça a relação de rota descoberta até o ponto de aterrissagem (linha tracejada ciano)
- Hover tip throttled via `requestAnimationFrame`
- Painel lateral: metadata do cabo/comutador, botões "Sondar" e "Ver Detalhes"

### Sonda Diária

- Inicia via botão ou API `daily_start`
- Sonda 699 cabos com TCP SYN-ACK, MTR (traceroute UDP), HTTP curl, ICMP
- Barra de progresso com live cable name, protocolo, latência e hops em tempo real
- Contadores por protocolo: TCP real, Sintético, MTR, ICMP, HTTP, Comutadores
- Tabela ao vivo com últimas 15 sondas: #, Cabo, Protocolo, Latência, Hops, Comutadores
- Múltiplas execuções por dia suportadas — logs com timestamp (`run_daily_HHMMSS.log`)
- Live JSON `.probe_live.json` com `log_file` para vinculação por run

### Sonda & Telemetria (por cabo)

- Histórico de janela: 6h / 24h / 3d / 7d / 30d
- Gráficos Plotly.js: latência, jitter, perda de pacotes, throughput, disponibilidade
- Barra de protocolos: contagem por TCP / Sintético / MTR / HTTP com filtro por clique
- Rastreamento hop-by-hop via MTR ao vivo
- Painel de geolocalização de comutadores

### Histórico

- Tabela de todas as amostras com filtros: status TCP/Sintético, faixa de latência, anomalias, data
- Pesquisa por cabo, protocolo, origem

### Busca de Cabos

- 9 modos: ID, Nome, Proprietário, Fornecedor, Ano, Status, Landing Point, Endpoint, País
- Visualização animada de busca binária

### Análise de Rotas / Risco / Cortes Críticos

- Top 3 rotas entre países com score de resiliência
- Mapa de risco de endpoints por país
- Identificação de cortes críticos (cabos que isolam conectividade)

---

## Pipeline de Sonda

```
run_daily_telemetry_tasks.sh
  ├── sonda.js              → TCP SYN-ACK + MTR + HTTP (Node.js, 699 cabos)
  └── rmifo_probe_multiprotocol.py
        ├── BGP mode        → RIPE STAT REST API
        ├── SNMP mode       → pysnmp-lextudio
        └── NetFlow mode    → Scapy passive capture (root opcional)
```

### Dependências Python

```
psycopg2-binary>=2.9
requests>=2.31
pysnmp-lextudio>=6.2
scapy>=2.5        # opcional, requer root para NetFlow
```

---

## Deploy (Railway)

```toml
# nixpacks.toml
[phases.setup]
nixPkgs = ["nodejs_20", "php82", "php82Extensions.pdo_pgsql", "mtr", "curl", "python312"]

[start]
cmd = "bash start.sh"
```

Variáveis de ambiente necessárias: `DATABASE_URL` ou `DATABASE_PUBLIC_URL`, `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`.

---

## PWA

Service Worker `sw.js` com estratégia cache-first para assets estáticos. API (`api.php`) sempre vai à rede — fallback `{ error: 'Offline' }` quando sem conexão. Instalável como app nativo em Android/iOS/Desktop.
