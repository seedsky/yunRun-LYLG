@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if exist "%~dp0runtime\python.exe" (
  "%~dp0runtime\python.exe" -X utf8 "%~dp0web.py" %*
) else (
  python -X utf8 "%~dp0web.py" %*
)
if errorlevel 1 pause
