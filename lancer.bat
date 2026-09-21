@echo off
rem ------------------------------------------------------------------
rem  Palettisation A400M - lancement depuis les sources (Windows)
rem  Prérequis : Python 3.10+ (https://www.python.org, cocher "Add to PATH")
rem ------------------------------------------------------------------
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist ".venv\Scripts\python.exe" (
    echo Creation de l'environnement virtuel...
    %PY% -m venv .venv || goto :erreur
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :erreur
)
".venv\Scripts\python.exe" main.py %*
goto :eof
:erreur
echo.
echo Echec de l'installation. Verifiez que Python 3.10+ est installe et accessible.
pause
