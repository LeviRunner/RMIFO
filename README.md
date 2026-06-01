# RMIFO — Resilience Map of International Fiber Optics

Plataforma open-source de monitoramento e análise de resiliência de cabos submarinos de fibra ótica. Coleta telemetria ativa (TCP, ICMP, MTR, HTTP, BGP), enriquece IPs e roteadores descobertos no caminho, persiste tudo em PostgreSQL e transforma os dados em análises no dashboard **RMIFO Radar**. Em produção no Railway com PWA offline e arquitetura de microsserviços de probe.

Produção: https://rmifo.up.railway.app

---

## Stack

| Camada | Tecnologia |
|---|---|
| Frontend | HTML5 · CSS3 · JavaScript (Vanilla) · Chart.js 4.4.3 |
| Backend | PHP 8.2 · PDO PostgreSQL |
| Banco de dados | PostgreSQL 16 |
| Probe API / Microsserviços | Node.js 20 · Express (pastas `api/` e `services/` do próprio repo) |
| Coleta | Python 3.12 · Node.js 20 · Bash |
| Deploy | Railway · Nixpacks · PWA (Service Worker `rmifo-v39`) |

Linguagens: JavaScript · PHP · Python · CSS · HTML · Shell.

---

## Estrutura de arquivos

Monorepo único: o site, a API de probe monolítica e os microsserviços convivem em pastas separadas na raiz, cada uma com seu próprio Root Directory no Railway.

```
RMIFO/
├── global-network-resilience/         ← site + dashboard (Root Directory: global-network-resilience)
│   └── ...                            (estrutura detalhada abaixo)
├── api/                               ← probe API monolítica (Root Directory: api)
│   ├── server.js                      ← todas as ferramentas num só serviço
│   ├── lib/                           ← tcp, icmp, mtr, http, bgp, switch, scanner, synthetic
│   ├── data/probe/                    ← dados de exemplo para teste sem banco
│   ├── package.json · railway.json · nixpacks.toml
└── services/                          ← microsserviços (Root Directory: services/<tool>)
    ├── tcp/  ping/  mtr/  http/  bgp/  switch/  telemetry/
    └── cada um: server.js · lib/ · package.json · railway.json · nixpacks.toml

global-network-resilience/             ← detalhe do site
    ├── web/
    │   ├── index.html                 ← SPA: dashboard Radar + barra lateral fixa
    │   ├── api.php                    ← backend REST (consultas analíticas + roteamento de probe)
    │   ├── assets/css/app.css
    │   ├── assets/js/app.js           ← núcleo da SPA (roteador, páginas, sonda)
    │   ├── assets/js/radar.js         ← dashboard Radar — 6 seções
    │   ├── assets/js/ui-enhancements.js ← visualizações (arc, scatter, heatmap, grafo)
    │   ├── sw.js                      ← Service Worker PWA
    │   └── manifest.json
    ├── db/
    │   ├── schema.sql                 ← definição das tabelas e relações
    │   └── seed.sql
    └── scripts/
        ├── sonda.js                   ← sonda TCP/MTR/Jitter/HTTP (Node.js)
        ├── rmifo_probe.py             ← motor de telemetria (Python)
        ├── rmifo_ip_enrich.py         ← enriquecimento nativo de IPs (DNS/WHOIS/TCP/TLS)
        ├── rmifo_advanced_reach.py    ← descoberta avançada de roteadores
        ├── rmifo_realtime_probe.py    ← medição real de latência
        ├── rmifo_analytics.py         ← rollup e exportação
        └── rmifo_catalog.py           ← gestão do catálogo de cabos
```


---

## Modelo de dados — como as tabelas se comunicam

O banco gira em torno da entidade **`cables`**. Cada cabo se conecta a pontos de aterrissagem, acumula amostras de telemetria ao longo do tempo, e cada amostra desdobra-se em saltos de rota geolocalizados.

```
nodes ──< cables >── cable_metadata (1:1)
            │
            ├──< cable_landing_points >── landing_points ──< endpoint_ip_samples
            │
            ├──< cable_telemetry_samples ──< cable_telemetry_hops ··> hop_switch_geolocations
            │                                            (liga por hop_ip)
            ├── cable_telemetry_reference (1:1, baseline)
            │
            └──< telemetry_alerts
```

### Caminhos de junção

| De | Para | Chave de junção | Cardinalidade |
|---|---|---|---|
| `cables` | `cable_landing_points` | `cable_id` | 1:N |
| `cable_landing_points` | `landing_points` | `endpoint_id` | N:1 |
| `landing_points` | `endpoint_ip_samples` | `endpoint_id` | 1:N |
| `cables` | `cable_telemetry_samples` | `cable_id` | 1:N |
| `cable_telemetry_samples` | `cable_telemetry_hops` | `sample_id` | 1:N |
| `cable_telemetry_hops` | `hop_switch_geolocations` | `hop_ip` (INET) | N:1 |
| `cables` | `cable_telemetry_reference` | `cable_id` | 1:1 |
| `cables` | `telemetry_alerts` | `cable_id` | 1:N |
| `cables` | `nodes` | `source_node_id` / `destination_node_id` | N:1 |

Toda relação direta com `cables` usa `ON DELETE CASCADE` — remover um cabo limpa suas amostras, hops, baseline, alertas e vínculos de aterrissagem. A geolocalização de hops (`hop_switch_geolocations`) é desacoplada por `hop_ip`: o mesmo roteador descoberto em vários cabos é enriquecido uma única vez e reaproveitado em todas as junções.

### O que cada tabela guarda

| Tabela | Conteúdo | Liga-se a |
|---|---|---|
| `cables` | 699 cabos catalogados (capacidade, comprimento, operadores) | núcleo |
| `cable_metadata` | metadata estendida do cabo | `cables` |
| `landing_points` | pontos de aterrissagem geolocalizados | — |
| `cable_landing_points` | junção cabo ↔ ponto de aterrissagem | `cables`, `landing_points` |
| `endpoint_ip_samples` | faixas/IPs amostrados por ponto de aterrissagem | `landing_points` |
| `cable_telemetry_samples` | medições por cabo no tempo (latência, jitter, perda, throughput, disponibilidade) | `cables` |
| `cable_telemetry_hops` | saltos MTR de cada amostra (RTT, perda por hop) | `cable_telemetry_samples` |
| `hop_switch_geolocations` | geo + enriquecimento de cada roteador (ASN, ISP, privacy, TLS) | via `hop_ip` |
| `cable_telemetry_reference` | baseline de referência por cabo (janela 7 dias) | `cables` |
| `telemetry_alerts` | anomalias detectadas | `cables` |
| `geoip_mapping` | mapeamento IP → país/cidade | autônoma |
| `nodes` | nós de topologia de roteamento | referenciada por `cables` |

---

## Funcionalidades

### RMIFO Radar — dashboard analítico

Integrado ao app, acessível pela barra lateral fixa à direita (recolhível, ícones SVG). Seções:

| Seção | Estado | Entrega |
|---|---|---|
| **Overview** | Completo | KPIs globais, distribuição de disponibilidade, latência média por região, alertas, piores cabos |
| **Connectivity** | Completo | Índice IQI (banda, latência, DNS) com percentis p25/p50/p75 e série temporal de 30 dias; Connection Quality (download/upload, latência idle/loaded, jitter, perda); tabela por cabo |
| **Routing** | Completo | Hops geolocalizados, top ISPs, distribuição de RTT, ASNs/países únicos, espaço IPv4 vs IPv6 |
| **Routing → RPKI** | Pronto* | Distribuição ROA Valid/Invalid/Unknown e validação por ASN |
| **Routing → Anomalies** | Completo | Conflitos MOAS (multi-origem) e risco de hijack (ROA invalid/not-found) |
| **Routing → Top ASes** | Completo | Ranking de sistemas autônomos por cabos atravessados, hops e IPs |
| **Tools** | Completo | TCP Probe, BGP Lookup, ASN Info, Top Rotas entre países |
| **IP Scanner** | Completo | Relatório unificado de um IP: TCP, HTTP, DNS, traceroute, BGP/ASN, tampering e enriquecimento |
| **Outage Center** | Completo | Linha do tempo de disrupções por dia/severidade e duração dos eventos |
| **Security** | Pronto* | Alertas por severidade, hops com flags VPN/Proxy/Tor, agregado de connection tampering |
| **Traffic** | Parcial | Throughput estimado, capacidade declarada, utilização |

\* *Pronto* = estrutura, action e frontend implementados; popula quando a coleta correspondente é ativada (ver [Ativação](#ativação--passo-a-passo)).

### Sonda Diária

Mede os 699 cabos com múltiplos protocolos calibrados (MTR, TCP SYN-ACK, TCP Jitter de 50 pacotes, HTTP, e fallback sintético). Mostra progresso ao vivo: arco animado, scatter de RTT em tempo real, barra de fases (pré-flight → ICMP → DNS → HTTP → pipeline), contadores por protocolo e tabela das últimas sondas. Cada execução grava amostras em `cable_telemetry_samples`, saltos em `cable_telemetry_hops` e novos IPs de roteador em `hop_switch_geolocations`.

### Histórico e telemetria por cabo

Janelas de 6h a 30d com gráficos de latência, jitter, perda, throughput e disponibilidade; heatmap de latência; filtro por protocolo, status (real/sintético) e anomalias; rastreamento hop-by-hop.

### Cortes Críticos e Análise de Rotas

Identifica cabos que isolam a conectividade entre dois países (grafo SVG interativo) e calcula as top rotas entre países com score de resiliência e mapa de risco por endpoint.

### Explorador DB

Navega e filtra qualquer tabela diretamente no browser, expondo inclusive os campos de enriquecimento de `hop_switch_geolocations`.

### Coleta e enriquecimento

- **Sonda ativa** (`sonda.js`): TCP/MTR/Jitter/HTTP sobre os cabos.
- **Enriquecimento nativo** (`rmifo_ip_enrich.py`): para cada IP descoberto extrai hostname (PTR), timezone, ASN/rota/tipo (WHOIS), flags de privacidade (VPN/Proxy/Tor/relay/hosting) e SANs TLS — usando apenas protocolos de rede padrão, sem APIs de terceiros.
- **Descoberta avançada** (`rmifo_advanced_reach.py`): alcança roteadores invisíveis a SNMP/ICMP por banners SSH/TLS/BGP, NETCONF/RESTCONF, dumps MRT (RouteViews/RIPE RIS), Looking Glass, IRR/WHOIS, MPLS, IXP/PeeringDB, gNMI e RIPE Atlas, com fingerprint de vendor (Cisco, Juniper, Arista, Huawei, Nokia, Ciena, Infinera, etc.).

---

## Uso de dados nas análises

Cada seção do Radar deriva sua análise de junções específicas, servidas pelo `api.php`:

| Seção | Origem dos dados | Análise produzida |
|---|---|---|
| Overview | `cable_telemetry_reference` ⋈ `cables` ⋈ `cable_landing_points` ⋈ `landing_points` | médias globais, distribuição por faixa, agregação de latência por região (a partir do país do 1º landing point) |
| Connectivity | `cable_telemetry_reference` ⋈ `cables` | disponibilidade e perda por cabo, contagem de degradados (<99%), correlação availability×loss |
| Routing | `cable_telemetry_hops` ⋈ `cables` ⋈ `hop_switch_geolocations` (por `hop_ip`) | ranking de ISPs no caminho, distribuição de RTT por hop, contagem de ASNs e países únicos |
| Security | `telemetry_alerts` ⋈ `cables` + `hop_switch_geolocations` (flags `privacy_*`) | alertas por severidade, roteadores anonimizadores no caminho |
| Traffic | `cable_telemetry_reference` ⋈ `cables` | throughput estimado, utilização vs `capacity_gbps`, throughput×latência |
| Tools | probe ao vivo (microsserviços) + RIPE STAT | medições sob demanda, não tocam o banco |

A baseline (`cable_telemetry_reference`) é recalculada por janela de 7 dias a partir de `cable_telemetry_samples`, servindo de referência estável para detectar desvios e alimentar as seções analíticas sem varrer todo o histórico.

---

## Probe API e microsserviços (monorepo)

As ferramentas de medição ativa (TCP, ICMP/ping, MTR, HTTP, BGP, switch, telemetry, DNS) vivem no próprio repositório, em duas formas que você escolhe deployar:

- **`api/`** — monolito: um único serviço com todas as ferramentas. Mais simples de operar; ideal para começar.
- **`services/<tool>/`** — microsserviços: cada ferramenta isolada, escalável em paralelo. Adicionar uma nova não toca nas existentes.

O `api.php` atua como gateway: seções de dados (Overview, Connectivity, Routing, Security, Traffic) consultam o PostgreSQL diretamente; ações de medição (`?action=probe_tcp`, `probe_bgp`, `probe_asn`, `probe_health`) são delegadas por HTTP. Para cada ferramenta, o gateway tenta primeiro o microsserviço dedicado e cai no monolito se ele não estiver configurado.

### Configuração no Railway

Cada pasta é um serviço Railway separado, definido pelo **Root Directory**:

| Serviço | Root Directory | Healthcheck |
|---|---|---|
| Site + dashboard | `global-network-resilience` | — |
| Probe API (monolito) | `api` | `/health` |
| Microsserviço por ferramenta | `services/tcp`, `services/ping`, `services/mtr`, `services/http`, `services/bgp`, `services/switch`, `services/telemetry`, `services/dns` | `/health` |

### Variáveis no gateway (serviço do site)

Defina **uma** das abordagens (ou ambas — microsserviço tem prioridade, monolito é fallback):

```
# Monolito — uma URL atende todas as ferramentas
PROBE_API_URL=http://api.railway.internal:3000

# Microsserviços — uma URL por ferramenta que você subir
TCP_SERVICE_URL=http://tcp.railway.internal:3000
PING_SERVICE_URL=http://ping.railway.internal:3000
MTR_SERVICE_URL=http://mtr.railway.internal:3000
HTTP_SERVICE_URL=http://http.railway.internal:3000
BGP_SERVICE_URL=http://bgp.railway.internal:3000
SWITCH_SERVICE_URL=http://switch.railway.internal:3000
TELEMETRY_SERVICE_URL=http://telemetry.railway.internal:3000
DNS_SERVICE_URL=http://dns.railway.internal:3000
```

Ferramentas sem URL configurada simplesmente não são chamadas. Verifique o que está ativo em `?action=probe_health`.


## Ativação — passo a passo

A estrutura toda já está no código. Para deixar cada recurso funcional:

### 1. Banco de dados (uma vez, idempotente)
Aplica o schema com as colunas novas (`roa_status`, `dns_ms`, `tcp_reset_pct`, `tamper_suspected`):
```
psql $DATABASE_URL -f global-network-resilience/db/schema.sql
```

### 2. Subir os serviços de probe no Railway
Para cada ferramenta, crie um serviço apontando o **Root Directory** (ver tabela acima). Opções:
- **Rápido:** suba só `api/` (monolito) — atende todas as ferramentas de uma vez.
- **Granular:** suba os `services/<tool>/` que quiser escalar isolados.

Depois defina as variáveis no serviço do site (`PROBE_API_URL` e/ou `*_SERVICE_URL`). Confirme com `?action=probe_health`.

### 3. Ativar cada recurso

| Recurso | O que ativa | Passo |
|---|---|---|
| **IP Scanner, Tools** | já funcionam | só precisa dos serviços de probe no ar (passo 2) |
| **Top ASes, Anomalies (MOAS), Outage Center, IPv4/IPv6** | já funcionam | usam dados que já existem no banco |
| **RPKI** | coluna `roa_status` | no enriquecimento (`rmifo_ip_enrich.py`), chamar o serviço `bgp` (`/network/bgp`) por prefixo e gravar `roa_status` em `hop_switch_geolocations` |
| **DNS (card IQI)** | coluna `dns_ms` | na `sonda.js`, resolver o host via serviço `dns` (`/probe`) e gravar `dns_ms` em `cable_telemetry_samples` |
| **Connection tampering** | colunas `tcp_*` | na `sonda.js`, chamar `tamperProbe` (serviço tcp `/tamper`) e gravar `tcp_reset_pct`, `tcp_timeout_pct`, `tamper_suspected` |
| **Alertas / Security / Outage** | execução periódica | agendar `scripts/rmifo_anomaly_detect.py` |

### 4. Agendar a detecção automática de anomalias
Gera e resolve alertas comparando amostras recentes com a baseline. Ex. cron a cada 15 min:
```
*/15 * * * * DATABASE_URL=$DATABASE_URL python3 scripts/rmifo_anomaly_detect.py
```
Limiares ajustáveis por env: `LAT_MULT` (2.0), `LOSS_ABS` (5.0), `AVAIL_DROP` (1.0).

Enquanto um recurso não tem coleta ativa, ele aparece vazio com aviso ("sem dados" / "rode o enrich") — nada quebra.

## PWA

Service Worker (`rmifo-v39`) com cache-first para assets estáticos e assets versionados por query string (`app.css?v=4`, `radar.js?v=4`) para cache-busting. A API sempre vai à rede, com fallback offline. Instalável como app nativo em Android, iOS e Desktop.
