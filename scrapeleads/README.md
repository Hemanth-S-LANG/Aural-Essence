# IndiaMART Lead Scraper

Connects to your EXISTING logged-in Chrome session — no login required.

## How to run

### Step 1: Start Chrome with remote debugging (do this ONCE)
Close your existing Chrome first, then run this command in CMD (not PowerShell):

```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\ChromeDebug"
```

Then manually go to: https://seller.indiamart.com/messagecentre
Log in if needed (only first time).

### Step 2: Run the scraper
```
python scrapeleads/scrape_leads.py
```

### Output
- `scrapeleads/output/indiamart_leads.xlsx` — all leads with full details
- `scrapeleads/output/indiamart_leads.csv` — same data as CSV
- Checkpoint saved every 10 leads — safe to interrupt and resume

## What it extracts
- Buyer name, company name
- Phone number (if visible)
- City, State
- Message sent by buyer
- Product enquired about
- Quantity, Order Value
- Enquiry date/time
- All details from "View More" popup
