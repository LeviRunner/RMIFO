# README Canonico

Este e o README principal e canonico do workspace.

## RMIFO Web Viewer

Aplicacao web para busca e exploracao de cabos submarinos com interface HTML/PHP e backend PostgreSQL local.

## O que esta pronto

- Busca por cable_id com demonstracao visual de busca binaria.
- Filtros por nome, owners, ano, status operacional, endpoints e pais.
- Aba de detalhes com metadata do cabo, endpoints e amostras de IP publico.

## Stack

- Frontend: HTML, CSS, JavaScript em arquivo PHP unico.
- Backend: PHP 8.4 com PDO PostgreSQL.
- Banco: PostgreSQL local na porta 55432 por padrao.
- Importacao de dados: Python com psycopg2.

## Entradas principais

- global-network-resilience/implementacoes/busca_binaria/index.php
- global-network-resilience/implementacoes/teste.SQL
- global-network-resilience/scripts/bootstrap_test_db.sh
- global-network-resilience/scripts/start_web.sh

## Regras de organizacao

- Arquivos .md e .txt de topo ficam em /Notebooks.
- A pasta global-network-resilience/data/ e protegida e nao deve ser apagada sem confirmacao explicita.
- Tudo que for do site, incluindo HTML, PHP, JavaScript, CSS e arquivos correlatos, deve ficar em global-network-resilience/Mods/SITE.

## Guias complementares

- README_PROJECT.md: resumo estrutural do workspace.
- README_USAGE.md: uso operacional e regras de limpeza.
