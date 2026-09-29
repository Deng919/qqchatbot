@echo off
chcp 65001 >nul
"D:\CodexTools\python\Scripts\python.exe" "%~dp0scripts\start_web.py" %*
if errorlevel 1 pause
