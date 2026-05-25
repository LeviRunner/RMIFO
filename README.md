# RMIFO — Resilience Map of International Fiber Optics

Plataforma open-source de monitoramento e análise de cabos submarinos de fibra ótica. Coleta telemetria TCP/MTR/HTTP/BGP/SNMP e dados de descoberta avançada de roteadores e comutadores (SSH banner, TLS, gNMI, RIPE Atlas, RouteViews MRT, RIPE RIS, Looking Glass, IRR/WHOIS), persiste em PostgreSQL e exibe em globo 3D interativo via MapLibre GL JS. Implantada no Railway com suporte a PWA offline.

---

## Stack

| Camada | Tecnologia |
|---|---|
| Frontend | HTML5 · CSS3 · JavaScript (Vanilla) · MapLibre GL JS 4.7.1 · Chart.js 4.4.3 |
| Backend | PHP 8.2 · PDO PostgreSQL (`pdo_pgsql`) |
| Banco de dados | PostgreSQL 16 — Railway gerenciado / socket local |
| Deploy | Railway · Nixpacks · Node.js 20 |
| Pipeline de sonda | Python 3.12 · Node.js 20 · Bash |
| PWA | Service Worker (`rmifo-v24`) · Web App Manifest |

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
    │   ├── index.html                 ← SPA com 9 páginas
    │   ├── api.php                    ← Backend REST PHP (~2 600 linhas)
    │   ├── sw.js                      ← Service Worker PWA (rmifo-v24)
    │   ├── manifest.json
    │   └── assets/
    │       ├── css/app.css
    │       ├── js/app.js              ← SPA frontend (~3 200 linhas)
    │       └── js/ui-enhancements.js ← Visualizações live (arc, scatter, heatmap, grafo)
    ├── db/
    │   ├── schema.sql                 ← 13 tabelas PostgreSQL principais
    │   ├── seed.sql
    │   └── setup.sh
    └── scripts/
        ├── rmifo_pipeline.sh          ← Orquestrador principal (daily / pipeline / all)
        ├── sonda.js                   ← Sonda TCP/MTR/Jitter Node.js (699 cabos)
        ├── rmifo_probe.py             ← Motor unificado (telemetry / multi / validate / all)
        ├── rmifo_advanced_reach.py    ← Descoberta avançada Vetores A-G (1 800 linhas)
        ├── rmifo_analytics.py         ← Rollup + exportação CSV (rollup / export / all)
        ├── rmifo_catalog.py           ← Gerenciamento do catálogo (seed / import / history)
        ├── rmifo_dev.sh               ← Utilitários locais (postgres / bootstrap / web / all)
        └── requirements.txt
```

---

## Banco de dados

### Tabelas principais — `schema.sql`

| Tabela | Descrição |
|---|---|
| `cables` | 699 cabos catalogados com metadata completa |
| `landing_points` | ~1 900 pontos de aterrissagem geolocalizados |
| `cable_landing_points` | Relação cabo ↔ endpoint |
| `endpoint_ip_samples` | IPs amostrados por endpoint (ponto de aterrissagem) |
| `cable_telemetry_samples` | Amostras TCP/MTR/HTTP por cabo (UNIQUE cable_id + measured_at) |
| `cable_telemetry_hops` | Saltos hop-by-hop de MTR por amostra |
| `cable_telemetry_reference` | Baseline de referência por cabo |
| `hop_switch_geolocations` | Geolocalização de comutadores de rota descobertos |
| `geoip_mapping` | Mapeamento IP → país/cidade para geolocalização de hops |
| `nodes` | Nós de roteamento da topologia |
| `telemetry_alerts` | Alertas ativos e histórico |
| `api_rate_limit` | Controle de rate limit por IP |

### Tabelas de descoberta avançada — criadas por `rmifo_advanced_reach.py`

| Tabela | Vetor | Descrição |
|---|---|---|
| `switch_banners` | A | Banners SSH/TLS/Telnet/BGP-179 + fingerprint JA3S + headers HTTP |
| `netconf_state` | B | Estado XML/JSON coletado via NETCONF/RESTCONF (porta 830/8080) |
| `mrt_bgp_peers` | C | Roteadores BGP encontrados em dumps MRT (RouteViews + RIPE RIS) com BGP Communities |
| `mpls_lsr_map` | D | Label Switch Routers descobertos via MPLS traceroute |
| `ixp_peering_lan` | D | Switches de peering em LANs de IXPs (via PeeringDB) |
| `gnmi_telemetry_samples` | F | Telemetria OpenConfig via gNMI/gRPC (porta 57400/6030) |
| `ripe_atlas_measurements` | G | Medições públicas e traceroutes do RIPE Atlas |
| `looking_glass_routes` | C | Rotas BGP ao vivo via Looking Glass (HE + RIPE Stat) |
| `whois_irr_records` | C | Registros IRR/WHOIS passivos (RIPE, ARIN, APNIC) |

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
  - Verde — operacional
  - Roxo — com telemetria TCP verificada
  - Âmbar — com alerta ativo
  - Vermelho — inativo
  - Amarelo — sonda ao vivo
- Pontos de aterrissagem como círculos coloridos
- **Comutadores de rota** (hops geolocalizados) — pontos âmbar/vermelho por número de cabos associados
- Linhas comutador → landing point (traçado de rota descoberta, linha tracejada ciano)
- Hover tip throttled via `requestAnimationFrame`
- Painel lateral: metadata do cabo/comutador, botões "Sondar" e "Ver Detalhes"

### Sonda Diária

- Inicia via botão ou API `daily_start`
- Sonda 699 cabos com múltiplos protocolos e variações calibradas:
  - **MTR UDP** — 15 ciclos padrão (30 em sonda completa), orçamento 300 ciclos totais
  - **TCP SYN-ACK** — conexão na porta ativa, timeout 4 s
  - **TCP Jitter** — 50 pacotes por variação, portas abertas detectadas automaticamente
  - **HTTP/curl** — 3 tentativas, timeout 8 s
  - **Sintético** — fallback heurístico quando sem resposta real
- **Arc progress SVG** — arco animado mostra porcentagem e contagem `atual / 699`
- **Scatter ao vivo** — gráfico Chart.js plota RTT ms de cada cabo sondado em tempo real (colorido por faixa de latência)
- Barra de progresso com nome do cabo atual, chip de protocolo, latência e hops em tempo real
- Contador de pacotes animado: 0 → `var_target` por variação
- Barra de fases: PRÉ-FLIGHT → ICMP → DNS → HTTP → Pipeline → Sumário
- PRÉ-FLIGHT: checks de dependências, DNS, HTTP e conectividade (grid de probes ICMP)
- Contadores acumulados por protocolo: TCP SYN-ACK, TCP Jitter, Sintético, MTR, HTTP, Comutadores, Anomalias
- Tabela ao vivo com últimas 15 sondas: #, Cabo, Protocolo (chip colorido), Latência, Hops, Comutadores
- Múltiplas execuções por dia — logs com timestamp (`run_daily_HHMMSS.log`)
- Live JSON `.probe_live.json` com `var_counts` e `var_target` por variação

### Sonda & Telemetria (por cabo)

- Histórico de janela: 6h / 24h / 3d / 7d / 30d
- Gráficos de latência, jitter, perda de pacotes, throughput, disponibilidade
- Barra de protocolos: contagem por TCP / Sintético / MTR / HTTP com filtro por clique
- Rastreamento hop-by-hop via MTR ao vivo
- Painel de geolocalização de comutadores

### Histórico

- Toolbar com filtros: data, cabo, protocolo, status (Real/Sintético), faixa de latência, anomalias
- **Heatmap de latência** — barras horizontais coloridas por RTT antes da tabela (verde < 50ms → vermelho ≥ 300ms)
- Chips de vetor avançado (A/B/C/D/E) filtram cabos descobertos por cada método
- Sumário: total, TCP Real, Sintético, MTR, HTTP, Anomalias, horário de conclusão

### Cortes Críticos

- Identificação de cabos que isolam conectividade entre dois países
- **Grafo SVG interativo** — mostra origem, destino e cabos intermediários; cabos críticos em vermelho tracejado; badge de isolamento detectado
- Tabela de cabos críticos com contagem de rotas impactadas e nível de impacto

### Análise de Rotas / Risco

- Top 3 rotas entre países com score de resiliência
- Mapa de risco de endpoints por país: grau de conexão, alcance, risco, perda estimada, congestionamento

### Explorador DB

- Navega e filtra qualquer tabela PostgreSQL diretamente no browser
- Filtros por coluna/operador/valor, paginação, breadcrumb

---

## Pipeline de Sonda

```
rmifo_pipeline.sh daily
  ├── Pré-flight: deps + DNS + HTTP + ICMP reachability
  ├── sonda.js  →  TCP SYN-ACK + TCP Jitter (50 pkt) + MTR (15 ciclos) + HTTP curl
  │               → .probe_live.json (progress, var_counts, var_target)
  └── rmifo_probe.py telemetry  →  ingestão PostgreSQL

rmifo_pipeline.sh pipeline
  ├── rmifo_probe.py telemetry   → ingestão de telemetria
  ├── rmifo_analytics.py rollup  → rollup 1h/5m
  └── rmifo_analytics.py export  → CSV em data/local/log/telemetry/

rmifo_probe.py multi --mode bgp|snmp|netflow|full
  ├── BGP mode     → RIPE STAT REST API
  ├── SNMP mode    → pysnmp-lextudio
  └── NetFlow mode → Scapy passive capture (root opcional)
```

---

## Descoberta Avançada de Comutadores (`rmifo_advanced_reach.py`)

Sete vetores complementares que alcançam switches invisíveis a SNMP/MTR/ICMP:

| Modo CLI | Vetor | Técnica |
|---|---|---|
| `--mode banner` | A | SSH banner (porta 22) + TLS cert CN/SAN (443/8443) + Telnet banner (23) + BGP OPEN AS (179) + HTTP headers fingerprint + JA3S TLS hash |
| `--mode netconf` | B | NETCONF `<get-config>` (porta 830) + RESTCONF JSON (8080/443) |
| `--mode mrt` | C | RouteViews MRT dump — AS path + BGP Communities (type 8/32) |
| `--mode ris` | C | RIPE RIS bview dump — second source (`--rrc rrc00/rrc01`) |
| `--mode lg` | C | Looking Glass ao vivo — Hurricane Electric + RIPE Stat |
| `--mode whois` | C | IRR/WHOIS passivo — RIPE, ARIN, APNIC via `whois` subprocess |
| `--mode mpls` | D | MPLS traceroute UDP/3503 + TCP/443 — revela LSRs ocultos |
| `--mode ixp` | D | PeeringDB — scan de switches em peering LANs de IXPs |
| `--mode shodan` | E | Shodan API — sysDescr/banners sem gerar tráfego |
| `--mode dns` | E | DNS PTR bulk — extrai tipo (sw/cr/pe) e localidade |
| `--mode gnmi` | F | gNMI/gRPC OpenConfig (porta 57400/6030) — telemetria estruturada |
| `--mode atlas` | G | RIPE Atlas — lê medições públicas; cria traceroutes com `RIPE_ATLAS_KEY` |
| `--mode full` | A-G | Pipeline completo de todos os vetores |

**Fingerprint de vendor** (banners SSH/TLS/Telnet/Shodan): Cisco, Juniper, Arista, Huawei, Nokia, Extreme, MikroTik, Cumulus, **Ciena, Infinera, Coriant, Ribbon/GENBAND** (optical/DWDM).

---

## Variáveis de Ambiente

| Variável | Padrão | Descrição |
|---|---|---|
| `DATABASE_URL` / `PGHOST` etc. | — | Conexão PostgreSQL |
| `MTR_CYCLES` | `15` | Ciclos MTR por cabo (30 em full scan) |
| `MTR_BUDGET` | `300` | Orçamento total de ciclos MTR por execução |
| `TCP_JITTER_PKTS` | `50` | Pacotes por variação TCP Jitter |
| `NETCONF_USER` / `NETCONF_PASS` | — | Credenciais NETCONF/RESTCONF (Vetor B) |
| `GNMI_USER` / `GNMI_PASS` | — | Credenciais gNMI (Vetor F) |
| `RIPE_ATLAS_KEY` | — | Chave de API RIPE Atlas para criar medições (Vetor G) |
| `SHODAN_API_KEY` | — | Chave Shodan (Vetor E) |
| `PROBE_WORKERS` | `30` | Threads paralelas na descoberta avançada |
| `PROBE_TIMEOUT` | `5` | Timeout por conexão (segundos) |

---

## Dependências Python

```
psycopg2-binary>=2.9
requests>=2.31
pysnmp-lextudio>=6.2   # SNMP (Python 3.12+)
scapy>=2.5             # NetFlow passivo (root opcional)
ncclient>=0.6          # NETCONF/RESTCONF (Vetor B)
mrtparse>=2.2          # MRT dump parsing (Vetores C RouteViews + RIPE RIS)
# pygnmi>=0.8          # gNMI (Vetor F) — instalar se usar --mode gnmi
# shodan>=1.31         # Shodan (Vetor E) — instalar se tiver API key
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

O `start.sh` inicializa o PostgreSQL local via socket Unix e sobe o servidor PHP embutido. Em Railway, `DATABASE_URL` aponta para o PostgreSQL gerenciado.

---

## PWA

Service Worker `sw.js` (`rmifo-v24`) com estratégia cache-first para assets estáticos (`/`, `index.html`, `app.css`, `app.js`, `ui-enhancements.js`, manifest, ícones). API (`api.php`) sempre vai à rede — fallback `{ error: 'Offline' }` quando sem conexão. Instalável como app nativo em Android/iOS/Desktop.
