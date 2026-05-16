RMIFO Dashboard — Windows
=========================

Pré-requisito: Python 3.10 ou superior instalado.
Baixe em https://python.org/downloads — marque "Add Python to PATH".

EXECUÇÃO RÁPIDA
---------------
  Duplo-clique em run.bat
  (ou abra o Prompt de Comando e execute: run.bat)

Na primeira execução o script cria um ambiente virtual (.venv\) e
instala as dependências a partir do diretório deps\ local (sem internet).

CONFIGURAÇÃO DO BANCO (OPCIONAL)
---------------------------------
  set WORKSPACE_ROOT=C:\caminho\para\RMIFO
  set PG_PORT=55432
  set PGDATABASE=global_network_resilience
  set PGUSER=postgres
  run.bat

Sem banco de dados o dashboard abre em modo demo automático.

ESTRUTURA
---------
  dashboard.py    Código-fonte principal
  run.bat         Launcher (cria venv + instala + executa)
  deps\           Wheels Python pré-baixados (offline)
  .venv\          Ambiente virtual criado na primeira execução
