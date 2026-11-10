@echo off
chcp 65001 >nul
call "%~dp0..\程序文件\_launch.bat" --mode offline %*
