# rmifo sonda worker

Sonda diaria do RMIFO como servico agendado (Railway Cron). Faz probe real
(TCP connect + jitter, HTTP, MTR quando disponivel) dos cabos carregados do
banco e grava telemetria real em `cable_telemetry_samples` / `cable_telemetry_hops`
/ `cable_telemetry_reference`. Substitui o gatilho manual de Sonda Diaria do site.

## Por que isto resolve o "tudo sintetico"
O probe so cai em `synthetic` quando nenhum protocolo responde. No container web
(PHP, via exec) o probe nao roda de forma confiavel e cai em sintetico. Este worker
roda em Node puro: o TCP connect nativo funciona no Railway (nao precisa de
privilegios), entao cabos que respondem em 443/80/22 geram telemetria REAL
(`active_tcp_connect`, confidence 0.95).

## Configuracao no Railway
1. New Service -> GitHub Repo `LeviRunner/RMIFO`
2. Settings -> Root Directory -> `services/sonda`
3. Variables -> referencie o Postgres: `DATABASE_URL=${{Postgres.DATABASE_URL}}`
   (ou cole a connection string interna do banco)
4. Settings -> Cron Schedule -> ex. `0 6 * * *` (06:00 UTC diario)
   Importante: defina o cron no DASHBOARD, nao no railway.json
   (config-as-code de cron teve bug conhecido). Min. a cada 5 min, sempre UTC.
5. O servico roda, grava no banco e ENCERRA (exit 0) — requisito do cron.

## Variaveis uteis
- `DATABASE_URL` (obrigatoria)
- `PROBE_LIMIT` / `--limit` para limitar nº de cabos por execucao
- `FULL_SCAN=1` para varredura completa de portas

## Execucao manual (teste)
```
DATABASE_URL=... node sonda.js
```
