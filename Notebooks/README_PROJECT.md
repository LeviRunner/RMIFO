# README_PROJECT Canonico

Este e o resumo estrutural e canonico do workspace.

## Foco atual

- Interface HTML/PHP funcional de busca e detalhes de cabos.
- Banco PostgreSQL local para consultas.
- Importador Python para catalogo e enriquecimento de endpoints.

## Estrutura principal

- global-network-resilience/Mods/SITE/index.php
- global-network-resilience/implementacoes/teste.SQL
- global-network-resilience/scripts/install_postgres_ubuntu.sh
- global-network-resilience/scripts/start_local_postgres.sh
- global-network-resilience/scripts/bootstrap_test_db.sh
- global-network-resilience/scripts/import_catalog_data.py
- global-network-resilience/scripts/start_web.sh
- global-network-resilience/data/external/submarine_cables.csv
- global-network-resilience/data/external/submarine_cables_full.json
- global-network-resilience/data/external/geoip_database.csv

## Limpeza aplicada em 2026-04-24

Foram removidos componentes fora do fluxo web atual:

- Pipeline PySpark e modulos de simulacao.
- Notebooks exploratorios antigos do subprojeto.
- Dados brutos CAIDA em data/raw.
- Scripts auxiliares de exportacao e organizacao nao usados pela interface web.
- Artefatos de build C e arquivos de apoio sem uso no fluxo principal.

## Regras permanentes

- Nao apagar global-network-resilience/data.
- Nao remover datasets em global-network-resilience/data/external sem validacao previa.
- Manter arquivos .md e .txt de topo em /Notebooks.
- Tudo que pertence ao site deve ficar em global-network-resilience/Mods/SITE.

## Referencia operacional

O guia operacional canonico esta em README_USAGE.md.