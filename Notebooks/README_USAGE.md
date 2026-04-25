# README_USAGE Canonico

Este e o guia operacional e canonico do workspace.

## Regras vigentes

- Arquivos .md e .txt da raiz foram movidos para /Notebooks em 2026-04-24.
- Em futuras limpezas e reorganizacoes, manter esses arquivos documentais dentro de /Notebooks.
- A pasta global-network-resilience/data/ e protegida e nao deve ser apagada.
- Qualquer remocao dentro de global-network-resilience/data/ exige confirmacao explicita.
- Tudo que for do site, como HTML, PHP, JavaScript, CSS e arquivos correlatos, deve ficar em global-network-resilience/Mods/SITE.
- A pasta global-network-resilience/.local e exclusiva de runtime local e deve permanecer fora do Git.

## Dependencias

```bash
cd global-network-resilience
./scripts/install_postgres_ubuntu.sh
```

## Ambiente Python para importacao

```bash
cd global-network-resilience
python3 -m venv ../.venv
../.venv/bin/python -m pip install -r ../Notebooks/requirements.txt
```

## Criar e popular banco

```bash
cd global-network-resilience
./scripts/bootstrap_test_db.sh
```

O bootstrap:

- inicia PostgreSQL local;
- aplica schema de Mods/teste.SQL;
- importa catalogo e endpoints com scripts/import_catalog_data.py.
- cria estrutura de telemetria historica para analises futuras.

## Subir aplicacao web

```bash
cd global-network-resilience
./scripts/start_web.sh
```

Acesse:

- http://localhost:8080/

## Dados obrigatorios mantidos

- global-network-resilience/data/external/submarine_cables.csv
- global-network-resilience/data/external/submarine_cables_full.json
- global-network-resilience/data/external/geoip_database.csv

## Pipeline diario de telemetria

Execucao manual:

```bash
cd global-network-resilience
./scripts/run_daily_telemetry_tasks.sh
```

Cron diario sugerido (03:15 UTC):

```bash
cd global-network-resilience
chmod +x scripts/run_daily_telemetry_tasks.sh
(crontab -l 2>/dev/null; echo "15 3 * * * cd /workspaces/RMIFO/global-network-resilience && ./scripts/run_daily_telemetry_tasks.sh >> /workspaces/RMIFO/global-network-resilience/results/telemetry_cron.log 2>&1") | crontab -
```

Scripts envolvidos:

- scripts/ingest_telemetry_samples.py: coleta e grava amostras historicas com metadados de confianca.
- scripts/validate_telemetry_baseline.py: compara janela recente vs baseline e gera relatorios JSON/Markdown.
- scripts/run_daily_telemetry_tasks.sh: orquestra ingestao + validacao.

## Marcacao de origem

- Este arquivo e o guia oficial.
- README.md e README_PROJECT.md em /Notebooks sao os outros documentos oficiais.
- Os READMEs dentro de global-network-resilience servem apenas como apontadores curtos.

# Com permissão 

Comandos que funcionam agora:

1. cd /workspaces/RMIFO/global-network-resilience

2. ./scripts/start_local_postgres.sh

3. ss -ltnp | grep -E "55432|8080" || true

4. ./scripts/start_web.sh

5. "$BROWSER" http://localhost:8080/

Alternativa mais robusta (mesmo se perder permissão no futuro):

1. cd /workspaces/RMIFO/global-network-resilience

2. bash ./scripts/start_local_postgres.sh

3. bash ./scripts/start_web.sh

4. "$BROWSER" http://localhost:8080/