@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=%~dp0runtime\python\python.exe"
if not exist "%PY%" goto nopy

echo.
"%PY%" "%~dp0项目文件\doctor.py"
echo.
pause
exit /b 0

:nopy
echo.
echo [错误] 未找到项目内置运行时：
echo     %PY%
echo.
echo 请确认复制的是整个「脑机接口项目」文件夹（必须包含 runtime 子目录）。
echo 如果只拷了代码没拷 runtime，请重新完整复制。
echo.
pause
exit /b 1
