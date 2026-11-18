@echo off
rem Install or update the bot on a server over SSH (see deploy.ps1).
rem   deploy.bat [IP] [-Key path\to\key] [-Logs]
setlocal
set "OLDCP="
for /f "tokens=2 delims=:" %%a in ('chcp') do set "OLDCP=%%a"
if defined OLDCP set "OLDCP=%OLDCP: =%"
if defined OLDCP set "OLDCP=%OLDCP:.=%"
rem The server answers in UTF-8.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" %*
set "RC=%ERRORLEVEL%"
if defined OLDCP chcp %OLDCP% >nul
pause
exit /b %RC%
