@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "APP=%SCRIPT_DIR%AIBrowser.py"
set "LOCK_DIR=%TEMP%\PythonBrowserApp-AIBrowser.lock"

if not exist "%APP%" (
    echo [FEHLER] AIBrowser.py wurde nicht gefunden:
    echo          "%APP%"
    pause
    exit /b 1
)

rem mkdir ist atomar: existiert der Ordner bereits, laeuft schon eine Instanz.
mkdir "%LOCK_DIR%" 2>nul
if errorlevel 1 (
    echo [INFO] AIBrowser laeuft bereits. Es wird keine zweite Instanz gestartet.
    pause
    exit /b 1
)

py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON=py -3"
) else (
    python -c "import sys" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON=python"
    ) else (
        rd "%LOCK_DIR%" 2>nul
        echo [FEHLER] Python wurde nicht gefunden.
        echo          Installiere Python fuer Windows und aktiviere "Add Python to PATH".
        pause
        exit /b 1
    )
)

echo [INFO] Starte AIBrowser ...
%PYTHON% -u "%APP%"
set "EXIT_CODE=%ERRORLEVEL%"

rd "%LOCK_DIR%" 2>nul

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [FEHLER] AIBrowser wurde mit Fehlercode %EXIT_CODE% beendet.
    pause
)

exit /b %EXIT_CODE%
