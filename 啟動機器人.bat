@echo off
title Music Bot
cd /d "%~dp0"

set "PY=C:\Users\nelso\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"

echo ====================================
echo   Checking packages ...
echo ====================================
rem Install any missing packages (fast when everything is already installed)
"%PY%" -m pip install --disable-pip-version-check -q -r requirements.txt
rem Keep yt-dlp up to date - YouTube changes often and old versions fail to load songs
"%PY%" -m pip install --disable-pip-version-check -q -U "yt-dlp[default]"
echo.

echo ====================================
echo   Starting Music Bot ...
echo ====================================
echo.

"%PY%" main.py

echo.
echo ====================================
echo   Bot stopped or an error occurred.
echo   Read the messages above.
echo ====================================
pause
