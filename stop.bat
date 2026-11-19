@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist capture.pid (echo 실행 중인 캡쳐 프로그램이 없습니다. & timeout /t 2 >nul & exit /b)
set /p PID=<capture.pid
taskkill /PID %PID% /F >nul 2>nul
del capture.pid >nul 2>nul
echo 캡쳐 프로그램을 종료했습니다.
timeout /t 2 >nul
