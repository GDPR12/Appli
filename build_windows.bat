@echo off
rem ------------------------------------------------------------------
rem  Construit l'exécutable Windows autonome : dist\PaletteA400M\PaletteA400M.exe
rem  Nécessite Python 3.10+ ; PyInstaller et pywebview sont installés au besoin.
rem ------------------------------------------------------------------
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv || goto :erreur
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller || goto :erreur
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean PaletteA400M.spec || goto :erreur
echo.
echo Executable genere : dist\PaletteA400M\PaletteA400M.exe
echo Copiez le dossier dist\PaletteA400M complet sur le poste cible.
pause
goto :eof
:erreur
echo.
echo La construction a echoue.
pause
