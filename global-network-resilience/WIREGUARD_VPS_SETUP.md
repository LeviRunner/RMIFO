# WireGuard + Railway — Guia de Arquitetura e Instalação

> Solução completa para rodar WireGuard com Railway como painel de controle
> e um VPS externo como plano de dados.

---

## O problema central

Railway expõe **apenas HTTP/HTTPS (TCP)**. WireGuard é UDP puro na porta 51820.
Não há como contornar isso sem infraestrutura externa.

```
Railway → só TCP 80/443
WireGuard → UDP 51820 ← Railway bloqueia aqui
```

---

## A solução: duas camadas

Dividir a responsabilidade:

```
┌─────────────────────────────────────────────┐
│  PLANO DE CONTROLE (Railway)                │
│                                             │
│  PHP api_gateway.php   → billing, usuários  │
│  PostgreSQL            → banco de dados      │
│  Python traffic_collector.py → debita créditos│
└────────────────────┬────────────────────────┘
                     │ HTTPS (HTTP REST)
                     │ POST /peer/add
                     │ DELETE /peer/{pubkey}
                     │ GET /peers/traffic
                     ▼
┌─────────────────────────────────────────────┐
│  PLANO DE DADOS (VPS externo)               │
│                                             │
│  Python vps_control_api.py  → FastAPI       │
│  WireGuard wg0              → UDP 51820     │
│  iptables / NAT             → roteamento    │
└─────────────────────────────────────────────┘
                     │ UDP 51820
                     ▼
              [ Clientes WireGuard ]
```

**Railway** faz: cadastro, billing, geração de chaves, controle de créditos.
**VPS** faz: recebe peers WireGuard, roteia tráfego UDP, mede tráfego real.

---

## Pré-requisitos

| Componente | Requisito mínimo |
|---|---|
| Railway | Plano pago (Worker + Web) |
| VPS | 1 vCPU, 512 MB RAM, Ubuntu 22.04 |
| VPS — porta | UDP 51820 aberta no firewall |
| VPS — porta | TCP 8000 acessível pelo Railway (pode ser privada) |

---

## Passo 1 — Preparar o VPS

### 1.1 Instalar dependências

```bash
sudo apt update
sudo apt install -y wireguard python3 python3-pip ufw
pip3 install fastapi uvicorn psutil
```

### 1.2 Gerar chave do servidor WireGuard

```bash
wg genkey | tee /etc/wireguard/server_privkey | wg pubkey > /etc/wireguard/server_pubkey
chmod 600 /etc/wireguard/server_privkey
cat /etc/wireguard/server_pubkey   # guarde esse valor
```

### 1.3 Configurar interface wg0

```bash
# Criar interface
ip link add wg0 type wireguard
ip address add 10.88.0.1/16 dev wg0
wg set wg0 private-key /etc/wireguard/server_privkey listen-port 51820
ip link set wg0 up

# Ativar NAT (substitua eth0 pela sua interface de saída)
iptables -t nat -A POSTROUTING -s 10.88.0.0/16 -o eth0 -j MASQUERADE
echo 1 > /proc/sys/net/ipv4/ip_forward
```

### 1.4 Tornar persistente (systemd)

```bash
cat > /etc/systemd/system/wg0.service <<'EOF'
[Unit]
Description=WireGuard wg0
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c "ip link add wg0 type wireguard 2>/dev/null; ip address add 10.88.0.1/16 dev wg0 2>/dev/null; wg set wg0 private-key /etc/wireguard/server_privkey listen-port 51820; ip link set wg0 up; iptables -t nat -A POSTROUTING -s 10.88.0.0/16 -o eth0 -j MASQUERADE; echo 1 > /proc/sys/net/ipv4/ip_forward"
ExecStop=/bin/bash -c "ip link del wg0 2>/dev/null; iptables -t nat -D POSTROUTING -s 10.88.0.0/16 -o eth0 -j MASQUERADE 2>/dev/null || true"

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now wg0
```

---

## Passo 2 — API de controle no VPS

Crie o arquivo `/opt/rmifo/vps_control_api.py`:

```python
#!/usr/bin/env python3
"""
API de controle WireGuard — roda no VPS, chamada pelo Railway.
Autenticação: header X-Token com valor de VPS_API_TOKEN.
"""

import os, subprocess, re
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel

app = FastAPI(title="RMIFO VPS Control API")
TOKEN = os.environ.get("VPS_API_TOKEN", "troque-por-token-secreto")

def auth(x_token: str = Header(...)):
    if x_token != TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido")

class PeerRequest(BaseModel):
    pubkey: str       # chave pública do cliente
    client_ip: str    # ex: 10.88.0.2/32
    preshared_key: str | None = None

@app.post("/peer/add", dependencies=[Depends(auth)])
def add_peer(req: PeerRequest):
    cmd = ["wg", "set", "wg0",
           "peer", req.pubkey,
           "allowed-ips", req.client_ip]
    if req.preshared_key:
        # escreve psk em arquivo temporário (wg exige arquivo ou stdin)
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".psk") as f:
            f.write(req.preshared_key)
            psk_path = f.name
        cmd += ["preshared-key", psk_path]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return {"ok": True, "peer": req.pubkey[:12] + "..."}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=e.stderr.decode())

@app.delete("/peer/{pubkey}", dependencies=[Depends(auth)])
def remove_peer(pubkey: str):
    try:
        subprocess.run(["wg", "set", "wg0", "peer", pubkey, "remove"],
                       check=True, capture_output=True)
        return {"ok": True}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=e.stderr.decode())

@app.get("/peers/traffic", dependencies=[Depends(auth)])
def peers_traffic():
    """Retorna bytes enviados/recebidos por peer (wg show wg0 transfer)."""
    result = subprocess.run(["wg", "show", "wg0", "transfer"],
                            capture_output=True, text=True)
    peers = {}
    for line in result.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) == 3:
            pubkey, rx, tx = parts
            peers[pubkey] = {"rx_bytes": int(rx), "tx_bytes": int(tx)}
    return peers

@app.get("/server/pubkey", dependencies=[Depends(auth)])
def server_pubkey():
    pub = open("/etc/wireguard/server_pubkey").read().strip()
    return {"pubkey": pub}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

### 2.1 Serviço systemd para a API

```bash
cat > /etc/systemd/system/rmifo-api.service <<'EOF'
[Unit]
Description=RMIFO VPS Control API
After=network.target wg0.service

[Service]
User=root
WorkingDirectory=/opt/rmifo
Environment=VPS_API_TOKEN=troque-por-token-secreto
ExecStart=/usr/bin/python3 /opt/rmifo/vps_control_api.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now rmifo-api
```

### 2.2 Verificar se está funcionando

```bash
# No próprio VPS (substituir TOKEN pelo seu):
curl -H "X-Token: troque-por-token-secreto" http://localhost:8000/server/pubkey
# {"pubkey":"abc123..."}
```

---

## Passo 3 — Coletor de tráfego no Railway (Python Worker)

Crie `global-network-resilience/scripts/traffic_collector.py`:

```python
#!/usr/bin/env python3
"""
Coleta tráfego WireGuard do VPS e debita créditos dos usuários.
Roda junto com wg_provisioner.py no worker Railway.
"""

import os, time, psycopg2, requests

VPS_URL   = os.environ["VPS_API_URL"]    # ex: http://seu-vps:8000
VPS_TOKEN = os.environ["VPS_API_TOKEN"]
DB_URL    = os.environ["DATABASE_URL"]
CREDITS_PER_GB   = int(os.environ.get("CREDITS_PER_GB", "10"))
POLL_INTERVAL    = int(os.environ.get("TRAFFIC_POLL_SEC", "60"))

headers  = {"X-Token": VPS_TOKEN}
prev_rx  = {}   # pubkey → bytes acumulados anteriores
prev_tx  = {}

def db():
    return psycopg2.connect(DB_URL)

def fetch_traffic():
    r = requests.get(f"{VPS_URL}/peers/traffic", headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()   # {pubkey: {rx_bytes, tx_bytes}, ...}

def debit(tunnel_id: int, user_id: int, bytes_delta: int):
    credits = max(1, round(bytes_delta / (1024**3) * CREDITS_PER_GB))
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT debit_credits(%s, NULL, 'wg_traffic', %s, NULL, 0)",
                (user_id, credits)
            )
            ok = cur.fetchone()[0]
        conn.commit()
    if not ok:
        # suspender túnel por falta de crédito
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE wg_tunnels SET status='suspended' WHERE id=%s",
                    (tunnel_id,)
                )
            conn.commit()
        # remover peer do VPS
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT client_pubkey FROM wg_tunnels WHERE id=%s", (tunnel_id,))
                row = cur.fetchone()
        if row:
            requests.delete(f"{VPS_URL}/peer/{row[0]}", headers=headers, timeout=10)

def run():
    print("[traffic] Iniciando coletor de tráfego...")
    while True:
        try:
            traffic = fetch_traffic()
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, user_id, client_pubkey FROM wg_tunnels WHERE status='active'"
                    )
                    tunnels = cur.fetchall()

            for tunnel_id, user_id, pubkey in tunnels:
                if pubkey not in traffic:
                    continue
                rx = traffic[pubkey]["rx_bytes"]
                tx = traffic[pubkey]["tx_bytes"]
                total = rx + tx

                if pubkey in prev_rx:
                    delta = max(0, total - (prev_rx[pubkey] + prev_tx.get(pubkey, 0)))
                    if delta > 0:
                        debit(tunnel_id, user_id, delta)

                prev_rx[pubkey] = rx
                prev_tx[pubkey] = tx

        except Exception as e:
            print(f"[traffic] Erro: {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    run()
```

---

## Passo 4 — Integrar PHP com a API do VPS

Adicione ao `web/api_gateway.php` a função auxiliar e atualize `handleTunnelCreate`:

```php
// ── Chama a API do VPS ────────────────────────────────────────────────────────
function vpsApi(string $method, string $path, array $body = []): array {
    $url   = rtrim(env('VPS_API_URL', ''), '/') . $path;
    $token = env('VPS_API_TOKEN', '');
    $ch    = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT        => 10,
        CURLOPT_CUSTOMREQUEST  => strtoupper($method),
        CURLOPT_HTTPHEADER     => ['Content-Type: application/json', "X-Token: $token"],
    ]);
    if ($body) curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($body));
    $raw  = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    if ($code >= 400 || $raw === false) err(502, 'Erro na API do VPS.');
    return json_decode($raw, true) ?? [];
}

// ── Criar túnel — versão com VPS externo ─────────────────────────────────────
function handleTunnelCreate(int $uid): void {
    spend($uid, null, 'wg_tunnel_create', 50);

    // 1. Gerar par de chaves para o cliente
    $privkey = trim(shell_exec('wg genkey'));
    $pubkey  = trim(shell_exec("echo '$privkey' | wg pubkey"));
    $psk     = trim(shell_exec('wg genpsk'));

    // 2. Buscar próximo IP disponível (10.88.x.x)
    $stmt = db()->query("SELECT COALESCE(MAX(SPLIT_PART(client_ip,'.',3)::int),0)+1 FROM wg_tunnels");
    $thirdOctet = min((int)$stmt->fetchColumn(), 254);
    $stmt2 = db()->query("SELECT COALESCE(MAX(SPLIT_PART(client_ip,'.',4)::int),1)+1 FROM wg_tunnels WHERE SPLIT_PART(client_ip,'.',3)::int = $thirdOctet");
    $fourthOctet = (int)$stmt2->fetchColumn();
    if ($fourthOctet > 254) { $thirdOctet++; $fourthOctet = 2; }
    $clientIp = "10.88.$thirdOctet.$fourthOctet";

    // 3. Registrar peer no VPS
    $vps = vpsApi('POST', '/peer/add', [
        'pubkey'        => $pubkey,
        'client_ip'     => "$clientIp/32",
        'preshared_key' => $psk,
    ]);

    // 4. Buscar chave pública do servidor
    $serverPubkey = env('WG_SERVER_PUBKEY', '');
    if (!$serverPubkey) {
        $info = vpsApi('GET', '/server/pubkey');
        $serverPubkey = $info['pubkey'] ?? '';
    }

    // 5. Salvar no banco
    $label = body()['label'] ?? 'tunnel';
    $stmt3 = db()->prepare(
        "INSERT INTO wg_tunnels (user_id, label, client_ip, client_pubkey, status)
         VALUES (?,?,?,?,'active') RETURNING id, created_at"
    );
    $stmt3->execute([$uid, $label, $clientIp, $pubkey]);
    $row = $stmt3->fetch();

    // 6. Montar config para o cliente
    $wgHost = env('WG_SERVER_HOST', '');
    $wgPort = env('WG_SERVER_PORT', '51820');
    $config = "[Interface]\nPrivateKey = $privkey\nAddress = $clientIp/16\nDNS = 1.1.1.1\n\n"
            . "[Peer]\nPublicKey = $serverPubkey\nPresharedKey = $psk\n"
            . "Endpoint = $wgHost:$wgPort\nAllowedIPs = 0.0.0.0/0\n"
            . "PersistentKeepalive = 25\n";

    json_out(['tunnel' => ['id' => $row['id'], 'label' => $label, 'client_ip' => $clientIp,
                           'status' => 'active', 'created_at' => $row['created_at']],
              'config' => $config]);
}
```

---

## Passo 5 — Variáveis de ambiente

### Serviço Web (Railway)

```env
VPS_API_URL=http://SEU_VPS_IP:8000
VPS_API_TOKEN=mesmo-token-do-vps
WG_SERVER_HOST=SEU_VPS_IP
WG_SERVER_PORT=51820
WG_SERVER_PUBKEY=chave-publica-do-servidor-wg
```

> `WG_SERVER_PUBKEY` evita uma chamada HTTP extra ao criar cada túnel.
> Obtenha com: `cat /etc/wireguard/server_pubkey` no VPS.

### Worker Python (Railway)

```env
VPS_API_URL=http://SEU_VPS_IP:8000
VPS_API_TOKEN=mesmo-token-do-vps
CREDITS_PER_GB=10
TRAFFIC_POLL_SEC=60
```

### VPS (systemd)

```env
VPS_API_TOKEN=mesmo-token-do-vps
```

---

## Passo 6 — Atualizar worker.sh

O worker Railway não precisa mais de NET_ADMIN. Ele apenas roda os scripts Python:

```bash
#!/usr/bin/env bash
# worker.sh — Railway Worker Service
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[worker] Iniciando provisioner + coletor de tráfego..."

# Rodar os dois em paralelo
python3 "$SCRIPT_DIR/scripts/wg_provisioner.py" &
python3 "$SCRIPT_DIR/scripts/traffic_collector.py" &

# Aguardar qualquer processo encerrar (reiniciar via Railway)
wait -n
```

---

## Fluxo completo — criar um túnel

```
Usuário clica "Criar túnel" no dashboard
    ↓
dashboard.html → POST {"action":"tunnel_create","label":"home"}
    ↓
api_gateway.php:
  1. Verifica JWT + créditos (50 créditos)
  2. Gera keypair + PSK com `wg genkey`
  3. POST http://VPS:8000/peer/add  →  wg set wg0 peer ...
  4. Salva tunnel no PostgreSQL
  5. Retorna config WireGuard completa
    ↓
dashboard.html exibe config para o usuário copiar
    ↓
Cliente configura WireGuard localmente
    ↓
Cliente conecta via UDP 51820 diretamente ao VPS
    ↓
Python traffic_collector.py (Railway) polling a cada 60s:
  GET http://VPS:8000/peers/traffic
  → calcula delta bytes → debita créditos
```

---

## Fluxo completo — suspender por falta de crédito

```
traffic_collector.py detecta delta de tráfego
    ↓
debit_credits() retorna FALSE (saldo insuficiente)
    ↓
UPDATE wg_tunnels SET status='suspended'
    ↓
DELETE http://VPS:8000/peer/{pubkey}
    ↓
VPS remove peer → cliente perde conexão
    ↓
Usuário compra créditos no dashboard
    ↓
wg_provisioner.py detecta túnel 'suspended' com créditos > 0
    ↓
POST http://VPS:8000/peer/add  →  reconecta o peer
    ↓
UPDATE wg_tunnels SET status='active'
```

---

## Posso criar um protocolo proprietário?

Sim — a arquitetura de duas camadas funciona com **qualquer protocolo de túnel**.
Se quiser substituir o WireGuard, a vantagem principal seria rodar o servidor de
túnel **diretamente no Railway** usando WebSocket (TCP 443), eliminando o VPS.

### Opção mais simples: WebSocket + TLS

```
Cliente JS/Python ──── WSS 443 ───▶ Railway Worker (Python)
                                          │
                                     TUN interface
                                     (NET_ADMIN no Railway)
                                          │
                                     internet / proxy
```

O servidor Python cria uma interface `tun0`, lê pacotes IP, encripta com
`cryptography` (ChaCha20-Poly1305) e transmite sobre WebSocket.
O cliente (app nativo ou extension do browser) faz o caminho inverso.

**Prós:** funciona 100% no Railway (sem VPS), sem dependência de `wg`.
**Contras:** mais complexo de implementar; clientes nativos precisam de app
customizado (não funciona com o cliente WireGuard padrão).

Para a maioria dos casos, manter WireGuard no VPS + Railway como plano de
controle é a rota mais rápida e segura para produção.

---

## Solução de problemas

### Peer não aparece em `wg show wg0 peers`
- Verifique se a API do VPS está rodando: `systemctl status rmifo-api`
- Teste manualmente: `curl -H "X-Token: TOKEN" http://VPS:8000/server/pubkey`
- Verifique logs do Railway → Serviço Worker → **Logs**

### Tráfego não está sendo debitado
- Verifique se `VPS_API_URL` e `VPS_API_TOKEN` estão corretos no worker Railway
- Verifique se `traffic_collector.py` está rodando junto com `wg_provisioner.py`
- Confirme que o cliente está conectado: `wg show wg0 transfer` no VPS

### Clientes não conseguem conectar (UDP)
- Verifique firewall do VPS: `ufw allow 51820/udp`
- Confirme que `WG_SERVER_HOST` no Railway aponta para o IP público do VPS (não interno)
- Teste UDP: `nc -u SEU_VPS_IP 51820` (deve não recusar conexão)

### NET_ADMIN no Railway Worker (modo legado)
Se você quiser usar o worker Railway como plano de dados (sem VPS), ative
**Privileged Mode** em Settings → Deploy. Mas lembre-se: Railway não expõe
UDP, então os clientes WireGuard não conseguirão conectar ao worker diretamente.
