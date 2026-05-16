RMIFO Dashboard — macOS
=======================

Pré-requisitos:
  • Python 3.10+   →  brew install python
  • tkinter        →  brew install python-tk
  • Homebrew       →  https://brew.sh (para instalar os pré-requisitos acima)

EXECUÇÃO RÁPIDA
---------------
  chmod +x run.sh
  ./run.sh

Na primeira execução o script cria um ambiente virtual (.venv/) e
instala as dependências a partir do diretório deps/ local (sem internet).

CONFIGURAÇÃO DO BANCO (OPCIONAL)
---------------------------------
  export WORKSPACE_ROOT=/caminho/para/RMIFO
  export PG_SOCKET=/tmp
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
