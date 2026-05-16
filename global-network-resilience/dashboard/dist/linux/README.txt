RMIFO Dashboard — Linux
=======================

Pré-requisito: Python 3.10 ou superior instalado no sistema.

EXECUÇÃO RÁPIDA
---------------
  chmod +x run.sh
  ./run.sh

Na primeira execução o script cria um ambiente virtual (.venv/) e
instala as dependências a partir do diretório deps/ local (sem internet).

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
