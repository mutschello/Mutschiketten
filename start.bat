@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto deps

REM --- Python suchen (der Microsoft-Store-Platzhalter zaehlt nicht) ---
set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe""
if defined PY goto mkvenv

echo Python wurde nicht gefunden - wird jetzt installiert (einmalig, ca. 1-2 Minuten) ...
winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
  set "PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe""
  goto mkvenv
)
echo.
echo Automatische Installation hat nicht geklappt.
echo Bitte Python 3.12 von https://www.python.org/downloads/ installieren
echo (Haken bei "Add python.exe to PATH" setzen) und start.bat erneut starten.
start "" https://www.python.org/downloads/
pause
exit /b 1

:mkvenv
echo Erstelle Python-Umgebung ...
%PY% -m venv .venv
if errorlevel 1 (echo Fehler beim Anlegen der Umgebung. & pause & exit /b 1)

:deps
fc /b requirements.txt ".venv\installed.txt" >nul 2>&1
if not errorlevel 1 goto run
echo Installiere Pakete (einmalig) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q
if errorlevel 1 (echo Paketinstallation fehlgeschlagen. & pause & exit /b 1)
copy /y requirements.txt ".venv\installed.txt" >nul

:run
start "" ".venv\Scripts\pythonw.exe" main.py %*
