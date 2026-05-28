# Análise de Pacotes de Sonda — RMIFO

> Como os pacotes enviados pela plataforma são vistos por controles de tráfego,
> firewalls, IDS/IPS e sistemas DPI em cada variação de protocolo.

---

## Arquitetura de camadas envolvidas

```
┌─────────────────────────────────────────────────────────┐
│  L7  Aplicação    script decide O QUE enviar            │
│  L6  Apresentação TLS — cifra, SNI, certificado         │
│  L5  Sessão       controle de estado da conexão         │
│  L4  Transporte   TCP / UDP — porta, flags, sequência   │
│  L3  Rede         IP — TTL, protocolo, endereço         │
│  L2  Enlace       Ethernet — MAC, VLAN 802.1Q           │
│  L1  Físico       elétrico / óptico / rádio             │
└─────────────────────────────────────────────────────────┘
```

Cada protocolo de sonda gera um padrão distinto em cada camada.
Firewalls, IDS/IPS e DPI inspecionam de L2 a L7 e identificam ou bloqueiam
com base nesses padrões.

---

## Visão comparativa por variante

```
╔══════════════╦════════════╦═════════════╦════════════════════════╗
║ Variante     ║ Proto L4   ║ Estado TCP  ║ Assinatura DPI         ║
╠══════════════╬════════════╬═════════════╬════════════════════════╣
║ TCP SYN-ACK  ║ TCP        ║ HALF-OPEN   ║ port-scan / health chk ║
║ TCP Jitter   ║ TCP        ║ ESTABLISHED ║ unknown application    ║
║ MTR          ║ UDP / ICMP ║ stateless   ║ traceroute             ║
║ HTTP curl    ║ TCP + TLS  ║ ESTABLISHED ║ HTTPS / curl UA        ║
║ Sintético    ║ ICMP       ║ stateless   ║ ping / estimativa      ║
╚══════════════╩════════════╩═════════════╩════════════════════════╝
```

---

## Variante 1 — TCP SYN-ACK (`tcp_syn`)

### O que o script faz

Abre uma conexão TCP para uma porta do alvo (80, 443, 22…), mede o RTT do
handshake e fecha o socket **antes de enviar dados de aplicação**.

### Anatomia do pacote camada a camada

```
L1 — Físico
  Sinal elétrico/óptico
  Tamanho mínimo do frame: 64 bytes (padding) a 74 bytes (com header completo)
  Codificação: NRZ (cobre), PAM4 (fibra 400G), 802.11 OFDM (wi-fi)

L2 — Ethernet
  ┌──────────┬──────────┬───────────┬──────────────────────────────┐
  │ DST MAC  │ SRC MAC  │ EtherType │         payload IP           │
  │ (gateway)│ (origem) │  0x0800   │                              │
  └──────────┴──────────┴───────────┴──────────────────────────────┘
  VLAN tag 802.1Q inserido por switches ao longo do caminho

L3 — IP
  Versão   : 4 (IPv4) ou 6 (IPv6)
  TTL      : 64 (padrão Linux) → decrementado em cada roteador
  Protocolo: 6 (TCP)
  Src IP   : IP do servidor Railway
  Dst IP   : IP do endpoint do cabo submarino

L4 — TCP (pacote inicial)
  SRC port : efêmero (32768–60999 — kernel Linux)
  DST port : 80 / 443 / 22 (depende do cabo alvo)
  Flags    : SYN = 1
  Seq      : ISN aleatório (Initial Sequence Number)
  Window   : 64240 bytes
  Options  : MSS=1460, SACK_PERM, Timestamps, WScale=7

Resposta do alvo:
  Flags    : SYN=1 ACK=1
  Ack      : ISN+1
  RTT      : timestamp(ACK recebido) − timestamp(SYN enviado)

Fechamento:
  Script envia RST imediato — nunca há dados HTTP trafegando
```

### Sequência no wire (Wireshark)

```
0.000ms  → [SYN]     seq=0          len=0   (nosso pacote)
14.3ms   ← [SYN-ACK] seq=0  ack=1   len=0   (resposta do alvo)
14.3ms   → [RST]     seq=1          len=0   (fechamento imediato)

RTT medido = 14.3ms
```

### Como aparece para controles de tráfego

| Sistema | O que vê | Reação típica |
|---|---|---|
| Firewall stateful | Conexão TCP incompleta — SYN sem dados | Permite — parece health check |
| IDS / Snort | Muitos SYN em sequência para IPs distintos | Alerta se > 100 SYN/s |
| DPI (Palo Alto, Fortinet) | Sessão TCP com RST imediato após SYN-ACK | Classifica como "port scan" |
| QoS / traffic shaper | DSCP 0 — sem prioridade diferenciada | Fila best-effort |
| Repetidor óptico submarino | Frame elétrico/óptico opaco | Amplifica sem inspecionar |

---

## Variante 2 — TCP Jitter (`tcp_jitter`, `tcp-Npkt`)

### O que o script faz

Envia **N pacotes TCP consecutivos** para a mesma porta e mede a variação
de latência entre eles. O jitter é calculado como o desvio padrão dos RTTs
individuais de cada pacote.

### Anatomia do pacote camada a camada

```
L3 — IP
  Mesmo cabeçalho do SYN-ACK
  Pacotes enviados em rajada rápida (burst < 10ms entre eles)

L4 — TCP (por pacote i = 1 … N)
  Flags    : PSH + ACK (dados)
  Seq      : incrementado por pacote
  Timestamp: opção TCP tsval/tsecr usada para RTT individual
  Payload  : 1 byte (mínimo para forçar resposta PSH+ACK)

Sequência no wire:
  t= 0ms  → SYN
  t= 0ms  ← SYN-ACK
  t= 0ms  → ACK          (handshake completo)
  t= 1ms  → PSH+ACK [1]  payload=0x01
  t= 1ms  ← PSH+ACK [1]  ACK do dado
  t= 2ms  → PSH+ACK [2]  payload=0x01
  t= 2ms  ← PSH+ACK [2]
  …       (N vezes)
  t=Nms   → FIN

Jitter = stdev([ RTT₁, RTT₂, …, RTTₙ ])
```

### Exemplo com N=5 pacotes

```
Pacote 1 → RTT = 14.2ms
Pacote 2 → RTT = 14.8ms
Pacote 3 → RTT = 13.9ms
Pacote 4 → RTT = 15.1ms
Pacote 5 → RTT = 14.5ms

Média  = 14.5ms
Stdev  =  0.4ms  ← jitter baixo (cabo saudável)
```

Jitter > 5ms indica congestionamento ou perda de pacotes no segmento.

### Como aparece para controles de tráfego

| Sistema | O que vê | Reação típica |
|---|---|---|
| Firewall | Sessão TCP estabelecida com dados mínimos | Permite — sessão legítima |
| IDS | Payload de 1 byte repetido — padrão incomum | Alerta "data exfil" em regras rígidas |
| DPI | Sessão sem protocolo de aplicação reconhecível | Classifica como "unknown" / "generic TCP" |
| QoS | Sessão de curta duração — poucos bytes | Fila best-effort, sem marcação QoS |
| Cabo (repetidor EDFA) | N frames de ~60 bytes em < 10ms | Amplifica sem inspecionar |

---

## Variante 3 — MTR Traceroute (`mtr_udp`)

### O que o script faz

Envia pacotes UDP (ou ICMP Echo) com **TTL crescente** (1, 2, 3…).
Cada roteador que descarta o pacote por TTL=0 responde com
`ICMP Time Exceeded`, revelando seu endereço IP e o RTT até ele.

### Anatomia do pacote camada a camada

```
Pacote TTL=1 — descobre roteador hop 1:
─────────────────────────────────────────
L3 — IP
  Src  : IP Railway (Dallas)
  Dst  : IP do cabo alvo
  TTL  : 1   ← decrementado para 0 no primeiro salto
  Proto: 17 (UDP)

L4 — UDP
  SRC port: 33434      (porta clássica do traceroute)
  DST port: 33434 + offset por hop (identifica resposta)
  Payload : 16–32 bytes arbitrários

Roteador R1 recebe, TTL → 0, descarta e gera:
─────────────────────────────────────────────
L3 — ICMP (resposta do hop 1)
  Tipo   : 11 (Time Exceeded)
  Código : 0  (TTL=0 durante trânsito)
  Payload: cabeçalho IP original + primeiros 8 bytes UDP
  Src IP : IP do roteador R1  ← hop descoberto

Pacote TTL=2 — descobre hop 2:
  R1 decrementa TTL 2→1, encaminha
  R2 decrementa TTL 1→0, gera ICMP → hop 2 descoberto

…repete até TTL=N onde o destino final responde:
  UDP → ICMP Port Unreachable (tipo 3, código 3)
```

### Exemplo real — cabo AMERICAS-I (Miami → São Paulo)

```
Hop  1   TTL=1   10.0.0.1         0.3ms    gateway Railway (Dallas)
Hop  2   TTL=2   162.243.x.x      1.2ms    backbone DigitalOcean
Hop  3   TTL=3   64.86.x.x        2.1ms    Level3 / Lumen backbone
Hop  4   TTL=4   4.68.x.x         8.4ms    Level3 Dallas → Miami
Hop  5   TTL=5   209.58.x.x      14.3ms    NAP das Américas (Miami)
Hop  6   TTL=6   *               timeout   segmento submarino ← sem ICMP
Hop  7   TTL=7   177.52.x.x     142.8ms    Embratel São Paulo (emerge do cabo)
Hop  8   TTL=8   200.x.x.x      144.2ms    destino final
```

O hop `*` (timeout) é o segmento óptico submarino — os repetidores EDFA
não geram ICMP porque operam apenas em L1 físico.

### Como aparece para controles de tráfego

| Sistema | O que vê | Reação típica |
|---|---|---|
| Firewall | UDP porta 33434+ incomum | Muitos firewalls bloqueiam → hop aparece como `*` |
| IDS / Snort | TTL crescente = padrão de traceroute | Alerta "network reconnaissance" |
| DPI | Sequência ICMP TTL Exceeded de múltiplas origens | Identifica como traceroute automaticamente |
| Roteadores de borda | TTL=0 → geram ICMP próprio | Expõem IP interno do roteador de borda do cabo |
| Repetidor óptico (EDFA) | Frame óptico — opera em L1 apenas | Amplifica sem processar L3+ |

---

## Variante 4 — HTTP curl (`http_curl`)

### O que o script faz

Executa `curl HEAD https://endpoint_do_cabo` medindo o tempo até o
primeiro byte (TTFB — Time To First Byte). Usa apenas o método HEAD
para não transferir corpo de resposta.

### Anatomia do pacote camada a camada

```
L4 — TCP
  DST port : 443 (HTTPS) ou 80 (HTTP)
  Handshake: SYN → SYN-ACK → ACK (padrão)

L5/L6 — TLS 1.3 (se HTTPS)

  ┌── ClientHello (PLAINTEXT — visível por qualquer middlebox) ──┐
  │  Versão       : TLS 1.3                                      │
  │  SNI          : "americas-i.cable.net"  ← hostname visível  │
  │  Cipher suites: TLS_AES_256_GCM_SHA384, TLS_CHACHA20…       │
  │  Extensions   : ALPN (h2, http/1.1), key_share, psk_modes   │
  └─────────────────────────────────────────────────────────────┘

  ServerHello (após):
    Certificado enviado (X.509)
    Chave de sessão negociada (ECDHE)
    Dados cifrados a partir daqui

L7 — HTTP/1.1 (cifrado após TLS)
  HEAD / HTTP/1.1
  Host       : americas-i.cable.net
  User-Agent : curl/7.88.0
  Accept     : */*

  Resposta esperada:
  HTTP/1.1 200 OK
  Content-Type: text/html
  Content-Length: 1234
  (sem corpo — método HEAD)

TTFB = timestamp(primeiro byte da resposta) − timestamp(SYN enviado)
```

### O SNI no ClientHello — dado crítico para DPI

O SNI (Server Name Indication) é enviado em **texto puro** antes da
negociação TLS, permitindo que qualquer middlebox identifique o destino
sem descriptografar o tráfego:

```
ClientHello (sem cifra, visível no wire):
  Extension: server_name (tipo 0)
    List entry[0]:
      type   : host_name (0)
      length : 24
      name   : "americas-i.cable.net"
```

### Como aparece para controles de tráfego

| Sistema | O que vê | Reação típica |
|---|---|---|
| Firewall | TCP:443 — sessão TLS completa | Permite — parece navegação normal |
| DPI | SNI do ClientHello em plaintext | Identifica domínio sem quebrar TLS |
| IDS | User-Agent "curl" → automação | Alerta em ambientes corporativos rígidos |
| CDN / WAF (Cloudflare) | HEAD sem cookies ou execução de JS | Pode retornar challenge page / 403 |
| Cabo (SLTE) | Frame óptico cifrado — opaco em L3+ | Transparente |

---

## Variante 5 — Sintético / Heurístico (`synthetic`)

### O que é

Quando o alvo não responde a nenhum protocolo ativo, a latência é
**estimada** via ICMP Echo (ping) ou interpolada a partir dos hops MTR
conhecidos.

### Anatomia do pacote camada a camada

```
L3 — ICMP (protocolo 1)
  Tipo    : 8 (Echo Request)
  Código  : 0
  ID      : PID do processo (identifica a sessão)
  Seq     : incrementado por pacote enviado
  Payload : timestamp de 8 bytes (para calcular RTT preciso)
  Tamanho : 64 bytes padrão (payload=56 bytes)

Resposta do alvo:
  Tipo    : 0 (Echo Reply)
  ID      : mesmo da requisição
  Seq     : mesmo da requisição
  RTT     = timestamp(Reply recebido) − timestamp(Request enviado)

Se sem resposta ICMP → estimativa por propagação:
  Latência_estimada = distância_km / 200  (km/ms em fibra óptica)
  Ex: Dallas → São Paulo = 8.200km → ~41ms one-way → ~82ms RTT
  (valor sintético marcado como "heuristic" nos logs)
```

### Como aparece para controles de tráfego

| Sistema | O que vê | Reação típica |
|---|---|---|
| Firewall | ICMP Echo Request | Maioria permite; alguns bloqueiam ICMP por política |
| IDS | Ping flood se muitos/s | Alerta se > 10 pings/s para mesmo destino |
| DPI | ICMP tipo 8 — identificado como ping | Sem aplicação de regras de camada 7 |
| Rate limiter | ICMP geralmente com prioridade baixa | Pode ser descartado em congestionamento |

---

## Fluxo completo — exemplo real de um pacote MTR no cabo AMERICAS-I

Rastreando hop 6 (entrada do segmento submarino em Miami):

```
┌─ Script Python — Railway (Dallas TX) ──────────────────────────────┐
│  socket.sendto(b"RMIFO\x00\x01" + b"\x00"*9, ("177.52.x.x", 33434))│
│  socket.setsockopt(IPPROTO_IP, IP_TTL, 6)                          │
└────────────────────────────────────────────────────────────────────┘
         │
         ▼ serializado pelo kernel Linux

[L7] Payload   : b"RMIFO\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00"
[L4] UDP       : src=55234  dst=33434  len=27  chk=0xA3F2
[L3] IP        : src=10.x.x.x  dst=177.52.x.x  TTL=6  proto=17  len=55
[L2] Ethernet  : src=02:42:ac:11:00:02  dst=172.17.0.1  type=0x0800
[L1] NIC virtual → kernel → interface física Railway → fibra

  ──────────────────────────────────────────────────────────
  → Hop 1  Dallas router       TTL 6→5   encaminha
  → Hop 2  Level3 backbone     TTL 5→4   encaminha
  → Hop 3  Level3 Dallas-MIA   TTL 4→3   encaminha
  → Hop 4  Miami backbone      TTL 3→2   encaminha
  → Hop 5  NAP das Américas    TTL 2→1   encaminha
  → Hop 6  Roteador de entrada do cabo submarino:
           TTL 1→0  ← DESCARTA o pacote

           Gera ICMP Time Exceeded:
           ┌──────────────────────────────────────────────┐
           │ [L3] IP   src=209.58.x.x  dst=10.x.x.x      │
           │           proto=1 (ICMP)  TTL=64             │
           │ [L3] ICMP tipo=11  código=0                  │
           │           payload= IP hdr original           │
           │                  + 8 bytes UDP originais     │
           └──────────────────────────────────────────────┘
  ← Hop 5  NAP das Américas    encaminha resposta de volta
  ← Hop 4  Miami backbone      encaminha
  ← Hop 3  Level3 Dallas-MIA   encaminha
  ← Hop 2  Level3 backbone     encaminha
  ← Hop 1  Dallas router       entrega ao Railway
  ──────────────────────────────────────────────────────────

┌─ Script Python recebe o ICMP ──────────────────────────────────────┐
│  hop_ip = "209.58.x.x"  ← roteador de entrada do cabo identificado│
│  rtt    = 14.3ms                                                   │
│  Conclusion: segmento submarino começa neste IP em Miami           │
└────────────────────────────────────────────────────────────────────┘
```

---

## Por que os repetidores ópticos submarinos são invisíveis

Os repetidores EDFA (Erbium Doped Fiber Amplifier) instalados ao longo
do cabo no fundo do oceano operam exclusivamente em **L1 físico**:

```
┌──────────────────────────────────────────────────────────────┐
│  Repetidor EDFA — operação                                   │
│                                                              │
│  Sinal óptico atenuado (entrada)                            │
│       │                                                      │
│       ▼                                                      │
│  Bombeamento com laser 980nm ou 1480nm                       │
│  Meio de érbio amplifica o sinal original                    │
│       │                                                      │
│       ▼                                                      │
│  Sinal óptico amplificado (saída)                           │
│                                                              │
│  ✗ NÃO lê endereços IP                                      │
│  ✗ NÃO processa TTL                                         │
│  ✗ NÃO gera ICMP                                            │
│  ✗ NÃO inspeciona payload                                   │
│  ✓ APENAS amplifica fótons                                  │
└──────────────────────────────────────────────────────────────┘
```

Por isso o hop do segmento submarino sempre aparece como `*` (timeout)
no MTR — não há equipamento no cabo capaz de gerar ICMP.

---

## Resumo de identificação por sistema de controle

```
╔═══════════════════╦═══════════════════════════════════════════════╗
║ Sistema           ║ Capacidade de detecção                        ║
╠═══════════════════╬═══════════════════════════════════════════════╣
║ Firewall básico   ║ Porta + protocolo (L4)                        ║
║ Firewall stateful ║ Estado da sessão TCP + porta + direção        ║
║ IDS / IPS         ║ Assinaturas de padrão (SYN flood, traceroute) ║
║ DPI               ║ L7 completo: SNI, User-Agent, payload         ║
║ QoS / DSCP        ║ Marcação de prioridade no campo TOS do IP     ║
║ BGP / RTBH        ║ Bloqueia por prefixo IP de origem ou destino  ║
║ Repetidor EDFA    ║ Zero — opera em L1 físico apenas              ║
╚═══════════════════╩═══════════════════════════════════════════════╝
```
