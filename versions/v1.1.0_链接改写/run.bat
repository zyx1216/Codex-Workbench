@echo off
chcp 65001 >nul
cd /d %~dp0
start http://localhost:8000
C:\Users\zyx\.conda\envs\workbench\python.exe -m uvicorn app:app --reload --port 8000
pause
