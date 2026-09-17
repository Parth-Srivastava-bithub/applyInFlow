@echo off
echo ================================================================
echo  Starting AutoApply and LaTeX Compiler in Docker
echo ================================================================
echo.
docker compose up -d --build
echo.
echo ================================================================
echo  AutoApply is running!
echo ================================================================
echo.
echo  1. AutoApply Dashboard:
echo     Open http://localhost:5000 in your browser.
echo.
echo  2. Scraping Mode:
echo     AutoApply uses the Chrome Extension to scrape directly
echo     from your browser session (no local CDP needed).
echo.
echo  To view logs: docker compose logs -f
echo  To stop:      docker compose down
echo ================================================================
echo.
pause
