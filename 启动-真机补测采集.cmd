@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=%~dp0runtime\python\python.exe"
if not exist "%PY%" goto nopy

echo ============================================================
echo   真机补测采集（校准 3 段 + V4 肌电 2 段，共 5 段约 6 分钟）
echo ============================================================
echo  产出：项目文件\data\real_受试者_日期_时间.jsonl
echo        采完把这个文件发回给组长即可。
echo.
echo  【五段动作，务必按提示做】
echo    1 D2_A       闭眼静息            30 秒
echo    2 D2_B       睁眼看十字、不做任务 120 秒
echo    3 D2_C       闭眼放松深呼吸       120 秒   ^<-- 这一段的"闭眼"是关键
echo    4 V4_SILENT  静默不动、不说话     40 秒
echo    5 V4_TALK    正常音量说话或朗读   40 秒
echo.
echo  【采集中必须看到这两个提示，否则那一段等于白采】
echo    ✓ 首帧已到：signal=0          ^<- 链路真的在推数据
echo    ✓ 「D2_A」本段收到 N 帧       ^<- 本段真的落了盘
echo    出现「本段零帧」：立即停下，别接着往下采
echo.
echo  提示 1：配对后设备管理器会多两个 COM 口，选「传出 / 输出」那个。
echo  提示 2：COM1 是主板自带口，永远不要选（能打开但收不到数据）。
echo  提示 3：五段采完会自动收尾，不用按 Ctrl+C。
echo  提示 4：全程别说话（除了第 5 段），别碰头环，别伸懒腰。
echo.
set "COM="
set /p COM=请输入串口号（例如 COM5；直接回车 = 先列出当前串口）: 

if "%COM%"=="" (
  "%PY%" "%~dp0项目文件\doctor.py"
  echo.
  pause
  exit /b 0
)

set "SUB="
set /p SUB=受试者编号（直接回车 = sub01）: 
if "%SUB%"=="" set "SUB=sub01"

echo.
echo 正在连接 %COM% ...（Ctrl+C 可随时中止）
echo.
"%PY%" "%~dp0项目文件\collect_real.py" --port %COM% --baud 115200 --subject %SUB% --preset d2v4 --stop-after-preset
echo.
echo ============================================================
echo  采集结束。请把上面的 dump 文件路径（项目文件\data\real_*.jsonl）
echo  发回给组长。
echo ============================================================
pause
exit /b 0

:nopy
echo.
echo [错误] 未找到项目内置运行时：
echo     %PY%
echo 请完整复制「脑机接口项目」文件夹（含 runtime 子目录），不要只复制部分文件。
echo.
pause
exit /b 1
