@echo off
title Music Bot
cd /d "%~dp0"

echo ====================================
echo   Starting Music Bot ...
echo ====================================
echo.

"C:\Users\nelso\AppData\Local\Programs\Python\Python313\python.exe" main.py

echo.
echo ====================================
echo   Bot stopped or an error occurred.
echo   Read the messages above.
echo ====================================
pause