@echo off
chcp 65001 >nul
setlocal
title 关闭答辩演示服务

echo ============================================================
echo   关闭所有答辩演示服务
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$n=0; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*live_bridge.py*' } | ForEach-Object { Write-Host ('  结束服务进程 PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $n++ }; if ($n -eq 0) { Write-Host '  没有在跑的服务进程' } else { Write-Host ('  已结束 ' + $n + ' 个') }"

echo.
echo ------------------------------------------------------------
echo   下一步：
echo     - 浏览器里那个 127.0.0.1:8770 的标签页，直接关掉
echo     - 任务栏里残留的「答辩演示-数据服务」窗口会自动消失
echo     - 要重新演示，双击「启动-答辩演示.cmd」
echo ------------------------------------------------------------
echo.
pause
