@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 答辩演示 - 真机服务

set "PY=%~dp0runtime\python\python.exe"
if not exist "%PY%" goto nopy

netstat -ano | findstr ":8771" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto busy

echo ============================================================
echo   真机演示（独立端口 8771，不影响 8770 的模拟页面）
echo ============================================================
echo.
echo   前提：头环已开机、已在系统蓝牙里配对过、知道是哪个 COM 口。
echo   查 COM 口：打开设备管理器，看 端口(COM 和 LPT) 这一项
echo   提示：选名字里带 传出 或 输出 的那个；COM1 不要选。
echo.
set "COM="
set /p COM=请输入串口号（例如 COM5）: 
if not defined COM goto abort

echo.
echo 正在连接 %COM% ...
start "答辩演示-真机" /min "%PY%" "%~dp0项目文件\live_bridge.py" --source serial --serial-port %COM% --baud 115200 --port 8771
ping -n 4 127.0.0.1 >nul

if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" (
    start "" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" http://127.0.0.1:8771/
) else if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" (
    start "" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" http://127.0.0.1:8771/
) else (
    start "" http://127.0.0.1:8771/
)

echo.
echo ------------------------------------------------------------
echo   开了就是连上了。检查两处：
echo     1. 「电极接触」有数值（显示良好/一般）
echo     2. 帧计数在涨，曲线在动
echo   若提示"数据源已停止"：COM 口号错了，或头环没开机。
echo ------------------------------------------------------------
echo.
if defined SKIP_PAUSE goto end
pause
:end
exit /b 0

:busy
echo.
echo [提示] 端口 8771 已被占用，先关掉任务栏的「答辩演示-真机」窗口，再重试。
echo.
if defined SKIP_PAUSE goto end
pause
exit /b 1

:abort
echo.
echo 已取消（没有输入 COM 口）。
echo.
if defined SKIP_PAUSE goto end
pause
exit /b 1

:nopy
echo.
echo [错误] 没找到内置运行时，请用完整的「脑机接口项目」文件夹。
echo.
if defined SKIP_PAUSE goto end
pause
exit /b 1
