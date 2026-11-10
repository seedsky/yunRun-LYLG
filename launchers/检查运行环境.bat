@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0..\程序文件"
"runtime\python.exe" -X utf8 "tools\check_environment.py"
pause
