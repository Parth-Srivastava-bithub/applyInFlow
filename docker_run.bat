@echo off
echo ================================================================
echo  Starting AutoApply in Docker
echo ================================================================
echo.
docker compose up -d --build
echo.
echo ================================================================
echo  AutoApply is running!
echo ================================================================
echo.
echo  1. First-Time LinkedIn Login:
echo     Open http://localhost:6080 in your browser.
echo     Log in using your LinkedIn Email and Password.
echo     (Do not use 'Continue with Google' as Google OAuth blocks container browsers).
echo     Your cookies and session are permanently saved in ./chrome_profile.
echo.
echo  2. AutoApply Dashboard:
echo     Open http://localhost:5000 in your browser.
echo.
echo  To view logs: docker compose logs -f
echo  To stop:      docker compose down
echo ================================================================
echo.
pause
