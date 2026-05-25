@echo off
setlocal
set "APPDIR=%~dp0"
set "PYW=%APPDIR%taskbar_text_clipboard.pyw"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%PYW%"
    exit /b
)

where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw "%PYW%"
    exit /b
)

where py >nul 2>nul
if %errorlevel%==0 (
    start "" pyw "%PYW%"
    exit /b
)

echo Python was not found.
echo Install Python from https://www.python.org/downloads/
echo During install, check "Add python.exe to PATH".
pause
