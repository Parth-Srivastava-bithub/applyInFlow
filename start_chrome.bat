@echo off
echo ================================================================
echo  Starting Google Chrome with Remote Debugging Port 9222
echo ================================================================
echo.
echo Please close all other running Chrome windows before starting,
echo OR this command will launch a dedicated debugging window.
echo.

set CHROME_PATH="C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist %CHROME_PATH% set CHROME_PATH="C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not exist %CHROME_PATH% set CHROME_PATH="%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"

set PROFILE_DIR="%~dp0chrome_profile"

echo Using Chrome executable: %CHROME_PATH%
echo Using profile directory : %PROFILE_DIR%
echo.

start "" %CHROME_PATH% --remote-debugging-port=9222 --user-data-dir=%PROFILE_DIR% "https://www.linkedin.com"

echo Chrome launched!
echo 1. In the Chrome window that just opened, log into LinkedIn manually.
echo 2. Once logged in, run: py run.py
echo ================================================================
pause
