@echo off
cd /d "%~dp0"
py -3 -m venv .venv
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
echo.
echo Fertig. Starte die App mit start.vbs.
pause
