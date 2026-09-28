@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo 未找到 .venv，请先按 README 完成安装。
  pause
  exit /b 1
)

".venv\Scripts\python.exe" app_qt.py
if errorlevel 1 pause
