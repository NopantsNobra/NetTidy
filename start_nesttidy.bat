@echo off
setlocal
cd /d "%~dp0"
set "NESTTIDY_PORT=8766"
set "NESTTIDY_URL=http://127.0.0.1:%NESTTIDY_PORT%"
start "" "%NESTTIDY_URL%"
python -m nesttidy.app --host 127.0.0.1 --port %NESTTIDY_PORT%
if errorlevel 1 (
  echo.
  echo 理巢启动失败。请确认已经安装 Python 3.11 或更高版本，并在当前目录运行。
  pause
)
