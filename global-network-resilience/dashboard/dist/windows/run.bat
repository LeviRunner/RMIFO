@echo off
:: RMIFO Dashboard — Windows launcher
:: deps\ contem wheels para Python 3.12 (offline).
:: Para outras versoes de Python, instala automaticamente via PyPI.
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%.venv"

:: deps\ pode estar na mesma pasta (ZIP) ou em ..\..\deps\windows\ (repo)
set "DEPS_DIR=%SCRIPT_DIR%deps"
if not exist "%DEPS_DIR%" set "DEPS_DIR=%SCRIPT_DIR%..\..\deps\windows"

:: dashboard.py pode estar na mesma pasta (ZIP extraido) ou em ..\..\
set "MAIN=%SCRIPT_DIR%dashboard.py"
if not exist "%MAIN%" set "MAIN=%SCRIPT_DIR%..\..\dashboard.py"
if not exist "%MAIN%" (
    echo [RMIFO] dashboard.py nao encontrado em:
    echo         %SCRIPT_DIR%dashboard.py
    echo         %SCRIPT_DIR%..\..\dashboard.py
    echo Use o arquivo ZIP gerado por build.sh ou verifique a estrutura de pastas.
    pause & exit /b 1
)

:: ── Localiza Python (py.exe suporta multiplas versoes no Windows) ──────────
set "PY="
where py.exe >nul 2>&1 && set "PY=py.exe"
if not defined PY (
    for %%P in (python3.exe python.exe) do (
        if not defined PY (
            where %%P >nul 2>&1 && set "PY=%%P"
        )
    )
)
if not defined PY (
    echo [RMIFO] Python nao encontrado. Instale em https://python.org
    pause & exit /b 1
)

:: ── Verifica versao minima 3.10 ────────────────────────────────────────────
for /f "tokens=2 delims= " %%V in ('"%PY%" --version 2^>^&1') do set "PY_VER=%%V"
for /f "tokens=1,2 delims=." %%A in ("!PY_VER!") do (
    set "PY_MAJ=%%A" & set "PY_MIN=%%B"
)
if !PY_MAJ! LSS 3 goto :ver_err
if !PY_MAJ! EQU 3 if !PY_MIN! LSS 10 goto :ver_err
goto :ver_ok
:ver_err
echo [RMIFO] Python !PY_VER! detectado -- requer 3.10 ou superior.
pause & exit /b 1
:ver_ok
echo [RMIFO] Python !PY_VER! detectado.

:: ── Detecta se os wheels locais sao compativeis (cp312) ───────────────────
set "USE_LOCAL=0"
if exist "%DEPS_DIR%" (
    if "!PY_MIN!"=="12" if "!PY_MAJ!"=="3" set "USE_LOCAL=1"
)

:: ── Cria venv se necessario ───────────────────────────────────────────────
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [RMIFO] Criando ambiente virtual em %VENV_DIR% ...
    "%PY%" -m venv "%VENV_DIR%"
    if !errorlevel! NEQ 0 (
        echo [RMIFO] Falha ao criar venv.
        pause & exit /b 1
    )
)

set "PY_VENV=%VENV_DIR%\Scripts\python.exe"

:: ── Instala dependencias ─────────────────────────────────────────────────
:: Sempre usa --only-binary=:all: para evitar compilacao do fonte (sem pg_config).
:: psycopg2-binary e opcional: sem ele o dashboard abre em modo demo.

set "CORE_PKGS=matplotlib numpy networkx"
set "PG_PKG=psycopg2-binary"
set "FIND_LINKS="
if exist "%DEPS_DIR%" set "FIND_LINKS=--find-links "%DEPS_DIR%""

if "!USE_LOCAL!"=="1" (
    echo [RMIFO] Instalando dependencias locais offline ^(Python 3.12^) ...
    "%PY_VENV%" -m pip install --quiet --only-binary=:all: ^
        --find-links "%DEPS_DIR%" --no-index ^
        !CORE_PKGS! !PG_PKG!
    if !errorlevel! NEQ 0 (
        echo [RMIFO] Falha nos wheels locais -- tentando PyPI ...
        call :install_with_pg_fallback
    )
) else (
    if exist "%DEPS_DIR%" (
        echo [RMIFO] Python !PY_VER! -- usando PyPI ^(deps\ como cache^) ...
    ) else (
        echo [RMIFO] Instalando dependencias via PyPI ...
    )
    call :install_with_pg_fallback
)
goto :after_install

:install_with_pg_fallback
:: Tenta instalar tudo (apenas binarios pre-compilados)
"%PY_VENV%" -m pip install --quiet --only-binary=:all: !FIND_LINKS! !CORE_PKGS! !PG_PKG! >nul 2>&1
if !errorlevel! EQU 0 (
    echo [RMIFO] Dependencias instaladas ^(incluindo suporte a banco de dados^).
    exit /b 0
)
:: psycopg2-binary nao tem wheel para esta versao -- instala sem ele
echo [RMIFO] psycopg2-binary nao disponivel para Python !PY_VER!.
echo [RMIFO] Instalando sem suporte a banco ^(modo demo ativo^) ...
"%PY_VENV%" -m pip install --quiet --only-binary=:all: !FIND_LINKS! !CORE_PKGS!
if !errorlevel! NEQ 0 (
    echo [RMIFO] Erro ao instalar dependencias basicas.
    pause & exit /b 1
)
exit /b 0

:after_install

echo [RMIFO] Iniciando dashboard ...
"%PY_VENV%" "%MAIN%" %*
if !errorlevel! NEQ 0 pause
