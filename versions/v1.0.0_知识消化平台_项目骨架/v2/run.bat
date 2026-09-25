@echo off
REM 知识消化平台 v1.0 启动脚本：双击运行后浏览器自动打开。
chcp 65001 >nul
start "" "http://localhost:8000"
python -m uvicorn v2.app:app --reload --port 8000
