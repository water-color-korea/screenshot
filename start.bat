@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul || (echo [오류] 파이썬이 설치되어 있지 않습니다. python.org 에서 설치해 주세요. & pause & exit /b)
python -c "import PIL, pystray" 2>nul || (echo 필요한 패키지 설치 중... & python -m pip install pillow pystray)
for /f "delims=" %%i in ('python -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set "PYW=%%i"
start "" "%PYW%" "%~dp0capture.py"
echo 캡쳐 프로그램이 실행되었습니다. Ctrl+Shift+A 로 캡쳐하세요.
timeout /t 2 >nul
