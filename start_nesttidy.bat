@echo off
setlocal
cd /d "%~dp0"
set "NESTTIDY_PORT=8766"
python -m nesttidy.app --host 127.0.0.1 --port %NESTTIDY_PORT% --open-browser
set "NESTTIDY_RC=%ERRORLEVEL%"
if "%NESTTIDY_RC%"=="23" (
  echo.
  echo 端口 %NESTTIDY_PORT% 已被占用，请先关闭占用该端口的程序后重试。
  pause
  exit /b 23
)
if not "%NESTTIDY_RC%"=="0" (
  echo.
  echo 理巢启动失败。请确认已经安装 Python 3.11 或更高版本，并在当前目录运行。
  pause
  exit /b %NESTTIDY_RC%
)
