@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "TARGET=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\capture_autostart.bat"
for /f "delims=" %%i in ('python -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set "PYW=%%i"
(
echo @echo off
echo chcp 65001 ^>nul
echo start "" "%PYW%" "%~dp0capture.py"
) > "%TARGET%"
echo 윈도우 켤 때 캡쳐 프로그램이 자동으로 실행되도록 설정했습니다.
pause
