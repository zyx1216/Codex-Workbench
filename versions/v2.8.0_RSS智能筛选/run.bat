@echo off
cd /d %~dp0
call conda activate workbench
python -m uvicorn app:app --reload --port 8000