@echo off
rem UTF-8 console so Chinese log lines are readable.
set "PYTHONUTF8=1"
chcp 65001 >nul
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload