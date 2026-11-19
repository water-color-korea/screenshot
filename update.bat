@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 최신 버전 받는 중...
git pull
if exist capture.pid (
  set /p PID=<capture.pid
  call taskkill /PID %%PID%% /F >nul 2>nul
  del capture.pid >nul 2>nul
)
call start.bat
