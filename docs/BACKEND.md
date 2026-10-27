# Back-end: arquitetura e operação

Este documento descreve o código do repositório. [README principal](../README.md) · [Worker de sonda](../services/sonda/README.md) · [SaaS](../global-network-resilience/SAAS_SETUP.md)

## Componentes

| Componente | Entrada | Responsabilidade |
|---|---|---|
| Site e API analítica | `global-network-resilience/web/api.php` | Consultas PostgreSQL, análises e encaminhamento de ferramentas de probe. |
| Gateway SaaS | `global-network-resilience/web/api_gateway.php` | Ações do dashboard com JWT, rotas `/v1/` com API key, créditos e webhook Stripe. |
| Probe monolítica | `api/server.js` | Endpoints HTTP para TCP, ping, MTR, HTTP, DNS, BGP, switch e telemetria. |
| Serviços de probe | `services/<tool>/server.js` | Serviços separados por ferramenta com `/health`. |
| Worker | `services/sonda/sonda.js` | Coleta agendada e persistência de telemetria quando o banco está acessível. |
| Banco | `global-network-resilience/db/schema.sql` e `scripts/saas_schema.sql` | Tabelas e views de rede e tabelas/rotinas SaaS, respectivamente. |

`global-network-resilience/start.sh` tenta aplicar a migração SaaS e inicia o servidor PHP. A migração é chamada com `|| true`: falha na migração não impede o site de iniciar. Confirme a migração nos logs e no banco.

## Banco e configuração

A API analítica procura `DATABASE_URL`, depois `DATABASE_PUBLIC_URL`, depois variáveis `PGHOST`/`PGPORT`/`PGDATABASE`/`PGUSER`/`PGPASSWORD`; sem host usa socket local. O gateway SaaS e o worker usam `DATABASE_URL`. A API decide o SSL a partir da porta ou do host; o worker usa TLS sem verificar o certificado. Esses comportamentos não são equivalentes: verifique a conexão efetivamente usada no ambiente.

O schema principal é aplicado separadamente, por exemplo `psql "$DATABASE_URL" -f global-network-resilience/db/schema.sql`. Faça backup e revise o alvo antes de executar migrações em um banco existente. As tabelas são descritas no README principal; quantidades de registros dependem do banco.

## Chamadas de probe

O PHP escolhe `<TOOL>_SERVICE_URL` quando configurado, senão `PROBE_API_URL`. Essa escolha é por presença da configuração; uma falha do serviço dedicado não causa fallback automático ao monolito. A comunicação usa JSON por HTTP. A lista de variáveis e exemplos de URLs está no [README](../README.md#variáveis-no-gateway-serviço-do-site).

A API monolítica expõe `GET /health`, `POST /probe/{tcp,ping,traceroute,http,dns}`, `POST /probe/tcp/tamper`, rotas `/network/bgp`, `/network/switch/*` e `/telemetry/*`. Os serviços dedicados usam suas próprias rotas, como `/probe`, `/prefix` ou `/scan`; confira cada `server.js` antes de chamar diretamente. Probe por rede depende de ferramentas e permissões do ambiente, além de conectividade externa.

Lotes de switch e telemetria retornam `scan_id` e mantêm progresso em um `Map` na memória do processo. Os resultados se perdem ao reiniciar e são removidos duas horas após o fim; instâncias distintas não compartilham o mapa. A solicitação de cancelamento é observada entre itens, e o processamento atual define `completed` ao sair do loop mesmo quando foi cancelado. Não trate o status final como prova de que todos os itens foram processados.

## Segurança e limites observados

- Os servidores Node de probe não autenticam chamadas nos seus endpoints. O rate limit e a validação existentes no PHP não protegem acesso direto a esses serviços. Restrinja-os a rede confiável e implemente controles de acesso antes de expô-los.
- O rate limit do PHP usa janelas no PostgreSQL, mas ignora exceções de banco. O IP considera `X-Forwarded-For`; configure um proxy confiável que normalize esse cabeçalho. Não o considere uma proteção suficiente por si só.
- No gateway SaaS, a assinatura Stripe só é verificada quando `STRIPE_WEBHOOK_SEC` está definida; configure-a e revise a integração antes de habilitar cobrança. O registro de evento, a atualização da compra e o crédito não estão em uma única transação.
- O servidor PHP embutido é iniciado pelo script existente; verifique os requisitos de concorrência e exposição antes de usar em produção.

## Verificações locais

```bash
# Sem banco ou rede: análise sintática
php -l global-network-resilience/web/api.php
php -l global-network-resilience/web/api_gateway.php
node --check api/server.js
node --check services/sonda/sonda.js

# Probe monolítica: instalar dependências e iniciar em terminal separado
cd api && npm install && npm start
# Em outro terminal: curl http://localhost:3000/health
```

As checagens de sintaxe não validam migração, conectividade PostgreSQL, permissões de rede, persistência ou webhook. Para essas integrações, use banco e serviços de teste isolados e inspecione os resultados.
