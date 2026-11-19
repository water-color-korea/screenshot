@echo off
chcp 65001 >nul
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\capture_autostart.bat" >nul 2>nul
echo 자동 실행을 해제했습니다.
pause
