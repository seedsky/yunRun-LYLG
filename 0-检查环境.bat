@echo off
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0runtime\python.exe" -X utf8 "%~dp0tools\check_environment.py"
pause
