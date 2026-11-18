@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=%~dp0runtime\python\python.exe"
if not exist "%PY%" goto nopy

echo ============================================================
echo   实时状态面板  (http://127.0.0.1:8770)
echo ============================================================
echo   1 = 模拟数据（无需硬件，演示 / 答辩彩排用）
echo   2 = 真机数据（需已配对头环并知道 COM 号）
echo   3 = 回放已有 CSV（调参用）
echo.
set "MODE="
set /p MODE=请选择 [1/2/3]，直接回车 = 1 : 
if "%MODE%"=="" set "MODE=1"

if "%MODE%"=="1" goto mode_mock
if "%MODE%"=="2" goto mode_serial
if "%MODE%"=="3" goto mode_replay
echo 无效选择，按模拟数据处理。
goto mode_mock

:mode_mock
set "ARGS=--source mock"
goto launch

:mode_serial
echo.
echo 提示：选「传出 / 输出」那个 COM 口；COM1 不要选。
set "COM="
set /p COM=请输入串口号（例如 COM5）: 
if "%COM%"=="" goto abort
set "ARGS=--source serial --serial-port %COM% --baud 115200"
goto launch

:mode_replay
echo.
echo 可用的回放数据：
echo   直接回车 = 项目文件\data\demo_mock_8phase.csv（合成，8 相位 x 65 帧，约 10 分钟）
echo   也可粘贴自己录制的 CSV 完整路径（先用 export_csv.py 从 jsonl 转换）
set "CSV="
set /p CSV=CSV 路径（回车用包内演示数据）: 
if "%CSV%"=="" set "CSV=%~dp0项目文件\data\demo_mock_8phase.csv"
if not exist "%CSV%" goto csvmiss
set "ARGS=--source replay --csv "%CSV%" --loop"
goto launch

:csvmiss
echo.
echo [错误] 找不到文件：
echo     %CSV%
echo 包内演示数据应在 项目文件\data\demo_mock_8phase.csv
pause
exit /b 1

:launch
echo.
echo 正在启动数据服务 ...
start "脑机接口-数据服务" /min "%PY%" "%~dp0项目文件\live_bridge.py" %ARGS%
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8770/
echo.
echo 面板已在浏览器中打开。
echo 停止服务：关闭任务栏里最小化的「脑机接口-数据服务」窗口，或在其窗口按 Ctrl+C。
echo.
pause
exit /b 0

:abort
echo.
echo 已取消（未输入必要参数）。
pause
exit /b 1

:nopy
echo.
echo [错误] 未找到项目内置运行时：
echo     %PY%
echo 请完整复制「脑机接口项目」文件夹（含 runtime 子目录）。
echo.
pause
exit /b 1
