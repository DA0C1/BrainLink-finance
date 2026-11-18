$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -m pip install -r requirements.txt
python "..\盯盘任务_成员D_20261023.py"
