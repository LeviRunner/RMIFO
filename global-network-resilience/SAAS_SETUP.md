# RMIFO SaaS — Guia de Instalação

> Plataforma de inteligência de rede com modelo de créditos, túneis WireGuard e downloads de base de dados IP.

---

## Arquitetura

```
Railway Projeto
├── Serviço Web (PHP)
│   ├── web/api_gateway.php   ← API REST + endpoints do dashboard
│   ├── web/dashboard.html    ← Painel SaaS
│   └── start.sh              ← Migra banco → sobe servidor PHP
│
└── Serviço Worker (Python)
    ├── worker.sh             ← Inicia interface wg0 → roda daemon
    └── scripts/wg_provisioner.py ← Sincroniza peers WireGuard a cada 30s
```

```
Stripe
└── Webhook → POST /api_gateway.php?stripe_webhook=1
    └── Evento: checkout.session.completed
```

---

## Pré-requisitos

- Conta no [Railway](https://railway.app) com plano pago (necessário para workers e volumes)
- Conta no [Stripe](https://stripe.com) para cobranças (opcional para testes)
- Repositório `LeviRunner/RMIFO` conectado ao Railway

---

## Passo 1 — Banco de dados (automático)

O schema SaaS é aplicado **automaticamente** a cada deploy pelo `start.sh`.

Ele verifica se a tabela `users` existe. Se não existir, executa o arquivo
`scripts/saas_schema.sql` via PDO.

**Tabelas criadas:**

| Tabela | Função |
|---|---|
| `users` | Contas de usuário |
| `api_keys` | Chaves de acesso à API REST |
| `credit_packs` | Catálogo de pacotes de crédito |
| `credit_purchases` | Histórico de compras |
| `service_pricing` | Custo em créditos por serviço |
| `usage_log` | Log de todas as requisições |
| `wg_tunnels` | Túneis WireGuard provisionados |
| `db_downloads` | Histórico de downloads de BD |
| `webhook_events` | Idempotência dos webhooks Stripe |

> Nenhuma ação manual necessária. Apenas faça o deploy.

---

## Passo 2 — Variáveis de ambiente (Serviço Web)

No Railway → Serviço Web → aba **Variables** → **Raw Editor**, cole e ajuste:

```env
JWT_SECRET=troque_por_string_aleatoria_longa_aqui
ENCRYPT_KEY=chave_exatamente_32_caracteres_aqui
APP_BASE_URL=https://global-network-resilience.up.railway.app
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SEC=whsec_...
IPGEO_API_KEY=sua_chave_ipgeolocation_io
WG_SERVER_HOST=ip_publico_do_servidor_wireguard
WG_SERVER_PORT=51820
CDN_BASE=https://cdn.rmifo.io/databases
```

### Quais são obrigatórias para testar?

| Variável | Obrigatória para testar? |
|---|---|
| `JWT_SECRET` | **Sim** — sem ela o login não funciona |
| `ENCRYPT_KEY` | **Sim** — usada para criar túneis |
| `APP_BASE_URL` | **Sim** — URL de retorno do Stripe |
| `STRIPE_SECRET_KEY` | Não — só para cobranças reais |
| `STRIPE_WEBHOOK_SEC` | Não — só para confirmar pagamentos |
| `IPGEO_API_KEY` | Não — endpoints geo ficam indisponíveis sem ela |
| `WG_SERVER_HOST` | Não — só para criar túneis WireGuard |
| `CDN_BASE` | Não — só para downloads de BD |

### Como gerar valores seguros

**JWT_SECRET** e **ENCRYPT_KEY** — use qualquer gerador de senha forte.
Exemplos de ferramentas:
- [randomkeygen.com](https://randomkeygen.com) → "Fort Knox Passwords"
- Ou simplesmente invente uma sequência longa com letras, números e símbolos

> `ENCRYPT_KEY` precisa ter **exatamente 32 caracteres**.

---

## Passo 3 — Criar o Serviço Worker (WireGuard)

O provisionador WireGuard roda como um processo separado do servidor web.

### 3.1 Criar o serviço no Railway

1. No seu projeto Railway, clique em **"New Service"**
2. Selecione **"GitHub Repository"**
3. Escolha o repositório **RMIFO**
4. Clique em **Settings** no novo serviço

### 3.2 Configurar Root Directory

Em **Settings → Source → Add Root Directory**, digite:
```
global-network-resilience
```

> Sem isso, o comando `bash worker.sh` não encontra o arquivo.

### 3.3 Configurar o Builder

Em **Settings → Build → Builder**, selecione:
```
Nixpacks
```

> O Railway pode mostrar "Deprecated" — pode ignorar, funciona normalmente.
> É necessário para instalar Python e as dependências automaticamente.

### 3.4 Configurar o comando de inicialização

Em **Settings → Deploy → Custom Start Command**, coloque:
```
bash worker.sh
```

### 3.5 Adicionar variáveis de ambiente

Em **Variables → Raw Editor**, cole:
```env
WG_INTERFACE=wg0
WG_SUBNET=10.88.0.1/16
WG_SERVER_PRIVKEY_PATH=/etc/wireguard/server_privkey
JWT_SECRET=mesmo_valor_do_servico_web
ENCRYPT_KEY=mesmo_valor_do_servico_web
```

> O `DATABASE_URL` é compartilhado automaticamente entre serviços do mesmo projeto.

### 3.6 Ativar modo privilegiado (NET_ADMIN)

Em **Settings → Deploy**, ative:
```
Privileged Mode  →  ON
```

> Necessário para criar a interface de rede `wg0` e usar o comando `wg`.

---

## Passo 4 — Configurar o webhook do Stripe

Este passo conecta o Stripe ao servidor para confirmar pagamentos automaticamente.

1. Acesse [dashboard.stripe.com/webhooks](https://dashboard.stripe.com/webhooks)
2. Clique em **"Add endpoint"**
3. No campo URL, coloque:
   ```
   https://global-network-resilience.up.railway.app/api_gateway.php?stripe_webhook=1
   ```
4. Em **"Events to send"**, selecione:
   ```
   checkout.session.completed
   ```
5. Clique em **"Add endpoint"**
6. Na página do webhook criado, clique em **"Reveal signing secret"**
7. Copie o valor `whsec_...` e cole na variável `STRIPE_WEBHOOK_SEC` do Passo 2

---

## Passo 5 — Criar pacotes de crédito no Stripe

Para cada pacote de crédito, você precisa criar um produto no Stripe e linkar o `stripe_price_id`.

### 5.1 Criar produtos no Stripe

1. Acesse [dashboard.stripe.com/products](https://dashboard.stripe.com/products)
2. Para cada pacote, clique em **"Add product"**:

| Pacote | Preço sugerido | Créditos |
|---|---|---|
| Starter | R$ 4,99 | 1.000 |
| Pro | R$ 19,99 | 5.000 |
| Business | R$ 79,99 | 25.000 |
| Enterprise | R$ 249,99 | 100.000 |

3. Após criar, copie o **Price ID** (começa com `price_...`)

### 5.2 Atualizar o banco com os Price IDs

Execute no banco (via Railway → seu PostgreSQL → Query):
```sql
UPDATE credit_packs SET stripe_price_id = 'price_XXXXXXXX' WHERE name = 'Starter';
UPDATE credit_packs SET stripe_price_id = 'price_XXXXXXXX' WHERE name = 'Pro';
UPDATE credit_packs SET stripe_price_id = 'price_XXXXXXXX' WHERE name = 'Business';
UPDATE credit_packs SET stripe_price_id = 'price_XXXXXXXX' WHERE name = 'Enterprise';
```

---

## Passo 6 — Testar o sistema

### 6.1 Acessar o dashboard

```
https://global-network-resilience.up.railway.app/dashboard.html
```

### 6.2 Criar conta de teste

Clique em **"Criar conta"**, preencha nome, e-mail e senha.

> Contas novas recebem **100 créditos grátis** automaticamente para testar.

### 6.3 Verificar via API (opcional)

```bash
# Registrar usuário
curl -X POST https://global-network-resilience.up.railway.app/api_gateway.php \
  -H "Content-Type: application/json" \
  -d '{"action":"auth_register","name":"Teste","email":"teste@email.com","password":"senha123"}'

# Resposta esperada:
# {"token":"eyJ...","user":{"id":1,"email":"teste@email.com","credits":100}}
```

```bash
# Ver saldo e uso (substituir TOKEN pelo valor retornado acima)
curl -X POST https://global-network-resilience.up.railway.app/api_gateway.php \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer TOKEN" \
  -d '{"action":"account_usage"}'
```

---

## Passo 7 — Usar a API REST externa (com X-API-Key)

Para desenvolvedores que queiram integrar via API:

### 7.1 Criar uma chave API no dashboard

Acesse o dashboard → aba **"API & Chaves"** → **"Criar chave"**.
Copie a chave gerada (começa com `rmifo_`) — ela não é mostrada novamente.

### 7.2 Exemplos de chamadas

```bash
# Geolocalização de IP
curl -X POST https://global-network-resilience.up.railway.app/v1/geo/lookup \
  -H "X-API-Key: rmifo_suachaveaqui" \
  -H "Content-Type: application/json" \
  -d '{"ip":"8.8.8.8"}'

# Probe TCP
curl -X POST https://global-network-resilience.up.railway.app/v1/probe/tcp \
  -H "X-API-Key: rmifo_suachaveaqui" \
  -H "Content-Type: application/json" \
  -d '{"host":"google.com","ports":[80,443]}'

# MTR traceroute
curl -X POST https://global-network-resilience.up.railway.app/v1/probe/mtr \
  -H "X-API-Key: rmifo_suachaveaqui" \
  -H "Content-Type: application/json" \
  -d '{"dst":"8.8.8.8"}'
```

---

## Tabela de custos por serviço

| Serviço | Créditos | Endpoint |
|---|---|---|
| Geolocalização de IP | 1 | `POST /v1/geo/lookup` |
| Geo em lote (por IP) | 1 cada | `POST /v1/geo/bulk` |
| Segurança / Threat Intel | 2 | `POST /v1/security/check` |
| Lookup de ASN | 1 | `POST /v1/asn/info` |
| BGP / Visibilidade de prefixo | 3 | `POST /v1/bgp/prefix` |
| Probe TCP | 5 | `POST /v1/probe/tcp` |
| MTR Traceroute | 20 | `POST /v1/probe/mtr` |
| Criar túnel WireGuard | 50 | via dashboard |
| Download de base de dados | 500 | via dashboard |

---

## Solução de problemas

### Dashboard mostra erro ao fazer login
- Verifique se `JWT_SECRET` está definido no serviço web
- Verifique se o deploy concluiu com sucesso (aba Deployments)
- Veja os logs: Railway → Serviço Web → **Logs**

### Schema não foi criado
- Verifique os logs do deploy — procure por `[migrate]`
- Se aparecer erro de conexão, o `DATABASE_URL` não está configurado

### Túnel WireGuard não é criado
- Verifique se o serviço worker está rodando
- Verifique se **Privileged Mode** está ativado no worker
- Veja os logs do worker em Railway → Serviço Worker → **Logs**

### Pagamento Stripe não adiciona créditos
- Verifique se `STRIPE_WEBHOOK_SEC` está correto
- Verifique se a URL do webhook no Stripe aponta para o endereço certo
- Consulte **Stripe Dashboard → Webhooks → [seu endpoint] → eventos recentes**

---

## Fluxo completo de um pagamento

```
Usuário clica "Comprar"
    ↓
dashboard.html → POST {"action":"billing_checkout","pack_id":1}
    ↓
api_gateway.php cria sessão no Stripe
    ↓
Usuário é redirecionado para o Stripe
    ↓
Stripe processa o pagamento
    ↓
Stripe envia POST para /api_gateway.php?stripe_webhook=1
    ↓
api_gateway.php verifica assinatura → credita os créditos na conta
    ↓
Usuário volta para o dashboard com saldo atualizado
```
