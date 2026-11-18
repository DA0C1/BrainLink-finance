@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=%~dp0runtime\python\python.exe"
if not exist "%PY%" goto nopy

echo ============================================================
echo   数据采集（真机）
echo ============================================================
echo  提示 1：配对头环后，设备管理器会新增两个 COM 口。
echo          选「传出 / 输出」那个。
echo  提示 2：COM1 是主板自带口，永远不要选（能打开但收不到数据）。
echo  提示 3：数据保存在 项目文件\data\rec_日期_时间.csv
echo.
set "COM="
set /p COM=请输入串口号（例如 COM5；直接回车 = 先列出当前串口）: 

if "%COM%"=="" (
  "%PY%" "%~dp0项目文件\doctor.py"
  echo.
  pause
  exit /b 0
)

echo.
echo 正在连接 %COM% @115200 ...
echo 连上后请保持 15 秒不要关闭窗口：若 15 秒内没有任何数据，会自动提示换口。
echo 结束采集请按 Ctrl+C。
echo.
"%PY%" "%~dp0项目文件\serial_bridge.py" --port %COM% --baud 115200 --record
echo.
echo 采集已结束。
pause
exit /b 0

:nopy
echo.
echo [错误] 未找到项目内置运行时：
echo     %PY%
echo 请完整复制「脑机接口项目」文件夹（含 runtime 子目录）。
echo.
pause
exit /b 1
