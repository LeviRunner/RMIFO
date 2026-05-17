RMIFO Dashboard — Linux
=======================

Pré-requisito: Python 3.10 ou superior instalado no sistema.

Debian/Ubuntu — instale os pacotes necessários antes de executar:
  sudo apt install python3 python3-venv python3-tk

EXECUÇÃO RÁPIDA
---------------
  chmod +x run.sh
  ./run.sh

Na primeira execução o script cria um ambiente virtual (.venv/) e
instala as dependências automaticamente.

DEPENDÊNCIAS OFFLINE vs. ONLINE
---------------------------------
  deps/  contém wheels pré-baixados para Python 3.12 (cp312).

  Python 3.12  → instala offline a partir de deps/ (sem internet)
  Outras versões (3.10, 3.11, 3.13, 3.14 …) → instala via PyPI
                  (requer conexão na primeira execução)

  Sem psycopg2-binary disponível: dashboard abre em modo demo.

CONFIGURAÇÃO DO BANCO (OPCIONAL)
---------------------------------
Para conectar ao PostgreSQL exporte as variáveis antes de executar:

  export WORKSPACE_ROOT=/caminho/para/RMIFO
  export PG_SOCKET=/workspaces/RMIFO/.local/run
  export PG_PORT=55432
  export PGDATABASE=global_network_resilience
  export PGUSER=postgres
  ./run.sh

Sem banco de dados o dashboard abre em modo demo automático.

ESTRUTURA
---------
  dashboard.py    Código-fonte principal
  run.sh          Launcher (cria venv + instala + executa)
  deps/           Wheels Python pré-baixados (offline)
  .venv/          Ambiente virtual criado na primeira execução
