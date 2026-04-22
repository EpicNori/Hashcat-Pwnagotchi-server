@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0crackserver.ps1" %*
exit /b %ERRORLEVEL%
