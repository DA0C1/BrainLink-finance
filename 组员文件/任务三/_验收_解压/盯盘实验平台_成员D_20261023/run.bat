@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install -r requirements.txt
python "..\盯盘任务_成员D_20261023.py"
pause
