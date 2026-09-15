# PowerShell script to launch Chrome with Remote Debugging Port 9222
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host " Starting Google Chrome with Remote Debugging Port 9222" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Please close other open Chrome instances if you want to use your default profile," -ForegroundColor Yellow
Write-Host "or this will start with a dedicated profile in %USERPROFILE%\.chrome-playwright-session." -ForegroundColor Yellow
Write-Host ""

$chromePaths = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)

$chromeExe = $chromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $chromeExe) {
    Write-Host "Error: Could not find Google Chrome executable." -ForegroundColor Red
    exit 1
}

$profileDir = "C:\Users\user\Documents\GPU_AGGREGATE\chrome_cdp_profile"

Write-Host "Found Chrome at: $chromeExe" -ForegroundColor Green
Write-Host "Using Profile  : $profileDir" -ForegroundColor Green
Write-Host ""

Start-Process -FilePath $chromeExe -ArgumentList @(
    "--remote-debugging-port=9222",
    "--user-data-dir=$profileDir",
    "https://www.linkedin.com"
)

Write-Host "Chrome launched!" -ForegroundColor Cyan
Write-Host "1. In that Chrome window, log in to LinkedIn manually." -ForegroundColor White
Write-Host "2. Once logged in, run: py run.py" -ForegroundColor White
Write-Host "================================================================" -ForegroundColor Cyan
