@echo off
REM Baut eine eigenstaendige Mutschiketten.exe nach dist\
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (py -3 -m venv .venv 2>nul || python -m venv .venv)
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt pyinstaller || (pause & exit /b 1)
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --onefile --name Mutschiketten --collect-submodules barcode main.py
echo.
echo Fertig: dist\Mutschiketten.exe
pause
