@echo off
chcp 65001 >nul
cd /d %~dp0
python -m uvicorn app:app --reload --port 8000
