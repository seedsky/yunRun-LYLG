@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0runtime\python.exe" (
  "%~dp0runtime\python.exe" -X utf8 "%~dp0tools\check_environment.py"
) else (
  python -X utf8 "%~dp0tools\check_environment.py"
)
pause
