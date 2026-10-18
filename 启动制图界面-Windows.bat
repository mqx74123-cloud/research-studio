@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PROJ_NETWORK=OFF"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "MPLCONFIGDIR=%~dp0gui-work\font-cache"
if not exist "%~dp0gui-work\temp" mkdir "%~dp0gui-work\temp"
set "TEMP=%~dp0gui-work\temp"
set "TMP=%TEMP%"
if exist "%~dp0runtime\python.exe" (
  "%~dp0runtime\python.exe" -X utf8 "%~dp0launch_gui.py" %*
) else if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0launch_gui.py" %*
) else (
  echo 未找到本地运行环境。请阅读 README.md 的开发者安装说明，或下载 Releases 中的 Windows 离线包。
  echo 本脚本不会自动联网安装。
)
pause
