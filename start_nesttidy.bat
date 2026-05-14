@echo off
setlocal
cd /d "%~dp0"
set "NESTTIDY_URL=http://127.0.0.1:8765"
start "" "%NESTTIDY_URL%"
python -m nesttidy.app --host 127.0.0.1 --port 8765
if errorlevel 1 (
  echo.
  echo 理巢启动失败。请确认已经安装 Python 3.11 或更高版本，并在当前目录运行。
  pause
)
