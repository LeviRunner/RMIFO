RMIFO Dashboard — Windows
=========================

Pré-requisito: Python 3.10 ou superior instalado.
Baixe em https://python.org/downloads — marque "Add Python to PATH".

EXECUÇÃO RÁPIDA
---------------
  Duplo-clique em run.bat
  (ou abra o Prompt de Comando e execute: run.bat)

Na primeira execução o script cria um ambiente virtual (.venv\) e
instala as dependências automaticamente.

DEPENDÊNCIAS OFFLINE vs. ONLINE
---------------------------------
  deps\  contém wheels pré-baixados para Python 3.12 (cp312).

  Python 3.12  → instala offline a partir de deps\ (sem internet)
  Outras versões (3.10, 3.11, 3.13, 3.14 …) → instala via PyPI
                  (requer conexão na primeira execução)

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
