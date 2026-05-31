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
| Microsserviços | Node.js 20 · Express ([`rmifo-services`](https://github.com/LeviRunner/rmifo-services)) |
| Coleta | Python 3.12 · Node.js 20 · Bash |
| Deploy | Railway · Nixpacks · PWA (Service Worker `rmifo-v39`) |

Linguagens: JavaScript · PHP · Python · CSS · HTML · Shell.

---

## Estrutura de arquivos

```
RMIFO/
└── global-network-resilience/         ← raiz do deploy
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

Repositórios irmãos: **[rmifo-services](https://github.com/LeviRunner/rmifo-services)** (microsserviços de probe) e **RMIFO_TEST_API** (staging de integração).

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

Integrado ao app, acessível pela barra lateral fixa à direita (recolhível, ícones SVG). Seis seções:

| Seção | Estado | Entrega |
|---|---|---|
| **Overview** | Completo | KPIs globais, distribuição de disponibilidade, latência média por região, alertas, piores cabos |
| **Connectivity** | Completo | Disponibilidade e perda por cabo, scatter availability×loss, tabela filtrável/ordenável |
| **Routing** | Completo | Hops geolocalizados, top ISPs no caminho, distribuição de RTT, ASNs e países únicos |
| **Tools** | Completo | TCP Probe, BGP Lookup, ASN Info, Top Rotas entre países (probe sob demanda) |
| **Security** | Parcial | Alertas por severidade, hops com flags VPN/Proxy/Tor |
| **Traffic** | Parcial | Throughput estimado, capacidade declarada, utilização |

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

## Microsserviços de probe

As ferramentas de medição ativa rodam como microsserviços independentes ([`rmifo-services`](https://github.com/LeviRunner/rmifo-services)) — `tcp`, `ping`, `mtr`, `http`, `bgp`, `switch` e `telemetry`. Cada um é auto-contido e escala em paralelo.

**Utilização em produção:** o `api.php` atua como gateway. Seções de dados (Overview, Connectivity, Routing, Security, Traffic) consultam o PostgreSQL diretamente; ações de medição ativa (Tools, Sonda) são delegadas por HTTP ao microsserviço correspondente pela rede interna do Railway, configurada via variáveis `*_SERVICE_URL`. Ferramentas não provisionadas simplesmente não são chamadas, então o sistema degrada com elegância e novas ferramentas entram sem reiniciar as existentes.

---

## PWA

Service Worker (`rmifo-v39`) com cache-first para assets estáticos e assets versionados por query string (`app.css?v=4`, `radar.js?v=4`) para cache-busting. A API sempre vai à rede, com fallback offline. Instalável como app nativo em Android, iOS e Desktop.
