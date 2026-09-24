@echo off
rem Avvia NT8 Backtest Analyzer dal codice sorgente (richiede Python 3.10+).
rem Alla prima esecuzione crea l'ambiente .venv e installa le dipendenze.
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo.
    echo  Python non trovato.
    echo  Installa Python 3.10 o superiore da https://www.python.org/downloads/
    echo  ^(durante l'installazione spunta "Add python.exe to PATH"^)
    echo  oppure usa direttamente NT8BacktestAnalyzer.exe.
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Prima esecuzione: creo l'ambiente Python...
    %PY% -m venv .venv || goto :errore
)

fc /b requirements.txt ".venv\requirements.installati" >nul 2>nul
if errorlevel 1 (
    echo Installo le dipendenze, attendi qualche minuto...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :errore
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :errore
    copy /y requirements.txt ".venv\requirements.installati" >nul
)

start "" ".venv\Scripts\pythonw.exe" main.py %*
exit /b 0

:errore
echo.
echo  Installazione non riuscita: controlla la connessione internet e riprova.
pause
exit /b 1
