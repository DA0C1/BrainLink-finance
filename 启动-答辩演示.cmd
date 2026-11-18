@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 答辩演示 - 数据服务

set "PY=%~dp0runtime\python\python.exe"
if not exist "%PY%" goto nopy

rem ---- 端口占用检查（上次没关干净时最常见）----
netstat -ano | findstr ":8770" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto portbusy

echo ============================================================
echo   答辩演示 · 一键启动
echo ============================================================
echo.
echo [1/3] 启动数据服务（模拟数据）...
start "答辩演示-数据服务" /min "%PY%" "%~dp0项目文件\live_bridge.py" --source mock --port 8770
ping -n 4 127.0.0.1 >nul

echo [2/3] 打开面板...
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" (
    start "" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" http://127.0.0.1:8770/
) else if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" (
    start "" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" http://127.0.0.1:8770/
) else (
    start "" http://127.0.0.1:8770/
)

echo [3/3] 完成。
echo.
echo ------------------------------------------------------------
echo   演示三步：Alt+Tab 切过来，点「焦虑」格，等 3-7 秒，再 Alt+Tab 切回
echo   结束后：关闭任务栏里最小化的「答辩演示-数据服务」窗口
echo ------------------------------------------------------------
echo.
echo   若页面空白或一直转圈，把地址复制到 Edge 里重新打开：
echo   http://127.0.0.1:8770/
echo.
if defined SKIP_PAUSE goto end
pause
:end
exit /b 0

:portbusy
echo.
echo [提示] 端口 8770 已被占用——多半是上次的服务没关。
echo        1) 看任务栏有没有「答辩演示-数据服务」窗口，关掉它再双击本脚本；
echo        2) 或者直接打开 http://127.0.0.1:8770/ ，能用就别再启动。
echo.
if defined SKIP_PAUSE goto end
pause
exit /b 1

:nopy
echo.
echo [错误] 没找到内置运行时：
echo     %PY%
echo 说明这个文件夹不是完整包，请用整个「脑机接口项目」目录。
echo.
if defined SKIP_PAUSE goto end
pause
exit /b 1
