@echo off
:: RMIFO Dashboard — Windows launcher
:: Cria um venv isolado, instala dependências locais e executa o dashboard.
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%.venv"
set "DEPS_DIR=%SCRIPT_DIR%deps"
set "MAIN=%SCRIPT_DIR%dashboard.py"

:: Localiza Python
set "PY="
for %%P in (python3.exe python.exe py.exe) do (
    if not defined PY (
        where %%P >nul 2>&1 && set "PY=%%P"
    )
)
if not defined PY (
    echo [RMIFO] Python nao encontrado. Instale em https://python.org
    pause & exit /b 1
)

:: Cria venv se necessario
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [RMIFO] Criando ambiente virtual em %VENV_DIR% ...
    %PY% -m venv "%VENV_DIR%"
)

set "PY_VENV=%VENV_DIR%\Scripts\python.exe"

:: Instala dependencias locais
if exist "%DEPS_DIR%" (
    echo [RMIFO] Instalando dependencias locais de %DEPS_DIR% ...
    "%PY_VENV%" -m pip install --quiet ^
        --find-links "%DEPS_DIR%" ^
        --no-index ^
        matplotlib numpy psycopg2-binary networkx
) else (
    echo [RMIFO] deps\ nao encontrado -- instalando via PyPI ...
    "%PY_VENV%" -m pip install --quiet ^
        matplotlib numpy psycopg2-binary networkx
)

echo [RMIFO] Iniciando dashboard ...
"%PY_VENV%" "%MAIN%" %*
