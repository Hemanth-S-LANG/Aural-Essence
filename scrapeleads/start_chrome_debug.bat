@echo off
echo Launching Chrome with remote debug port 9222...
echo Your IndiaMART session will be preserved.
echo.
echo Once Chrome opens:
echo   1. Go to seller.indiamart.com/messagecentre
echo   2. Set the date filter (Apr 2025 to today)
echo   3. Then run: python scrapeleads/scrape_leads.py
echo.
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9222 ^
  --user-data-dir="C:\Users\Dell\IndiaMART_Profile" ^
  --no-first-run ^
  --no-default-browser-check ^
  --start-maximized ^
  --disable-blink-features=AutomationControlled
echo Chrome launched. You can close this window.
