@echo off
setlocal
cd /d "%~dp0"
echo === Twitch Clipper: a gerar TwitchClipper.exe ===

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY goto :nopython

if not exist .venv\Scripts\python.exe %PY% -m venv .venv || goto :fail
set "VPY=.venv\Scripts\python.exe"
%VPY% -m pip install --upgrade pip >nul
%VPY% -m pip install -r requirements.txt || goto :fail

if not exist ffmpeg.exe call :getffmpeg || goto :fail

%VPY% -m PyInstaller --noconfirm --onefile --windowed --name TwitchClipper ^
  --add-binary "ffmpeg.exe;." --collect-all streamlink app.py || goto :fail

copy /y dist\TwitchClipper.exe TwitchClipper.exe >nul
echo.
echo Pronto! O ficheiro TwitchClipper.exe esta nesta pasta.
pause
exit /b 0

:getffmpeg
echo A descarregar o ffmpeg...
curl -L --fail -o ffmpeg.zip https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip || exit /b 1
tar -xf ffmpeg.zip || exit /b 1
copy /y ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe ffmpeg.exe >nul || exit /b 1
rmdir /s /q ffmpeg-master-latest-win64-gpl
del ffmpeg.zip
exit /b 0

:nopython
echo Python nao encontrado. Instale em https://www.python.org/downloads/
echo (na instalacao, marque "Add python.exe to PATH") e corra este ficheiro de novo.
pause
exit /b 1

:fail
echo.
echo Algo falhou - veja as mensagens acima.
pause
exit /b 1
