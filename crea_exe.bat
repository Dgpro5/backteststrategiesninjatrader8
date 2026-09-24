@echo off
rem Crea dist\NT8BacktestAnalyzer.exe (eseguibile singolo, non serve Python per usarlo).
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Python non trovato: installalo da https://www.python.org/downloads/
    pause
    exit /b 1
)

if not exist ".venv-build\Scripts\python.exe" (
    %PY% -m venv .venv-build || goto :errore
)
".venv-build\Scripts\python.exe" -m pip install --upgrade pip || goto :errore
".venv-build\Scripts\python.exe" -m pip install -r requirements-dev.txt || goto :errore
".venv-build\Scripts\python.exe" -m PyInstaller --noconfirm --clean NT8BacktestAnalyzer.spec || goto :errore

echo.
echo  Fatto: dist\NT8BacktestAnalyzer.exe
echo.
pause
exit /b 0

:errore
echo.
echo  Creazione dell'eseguibile non riuscita.
pause
exit /b 1
