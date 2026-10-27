# IndiaMART Lead Manager Scraper
## Complete Technical Documentation

**Company**: Aural Essence Private Limited  
**Project**: B2B Lead Generation — IndiaMART Lead Manager  
**Prepared by**: SDE1 Intern  
**Date**: October 2026

---

## Table of Contents

1. [What the Scraper Does](#1-what-the-scraper-does)
2. [Tech Stack](#2-tech-stack)
3. [System Architecture](#3-system-architecture)
4. [The Core Problem — Why Normal Scraping Fails](#4-the-core-problem--why-normal-scraping-fails)
5. [How Chrome Connection Works (CDP)](#5-how-chrome-connection-works-cdp)
6. [The ReactVirtualized Problem & Solution](#6-the-reactvirtualized-problem--solution)
7. [Complete Scraping Flow A–Z](#7-complete-scraping-flow-az)
8. [JavaScript Injection — The Extraction Engine](#8-javascript-injection--the-extraction-engine)
9. [View More Modal — Deep Extraction](#9-view-more-modal--deep-extraction)
10. [Phone Number Processing](#10-phone-number-processing)
11. [Checkpoint & Resume System](#11-checkpoint--resume-system)
12. [Output Files & Formatting](#12-output-files--formatting)
13. [Anti-Detection Measures](#13-anti-detection-measures)
14. [File & Folder Structure](#14-file--folder-structure)
15. [How to Run](#15-how-to-run)
16. [Ethical Constraints](#16-ethical-constraints)
17. [Live Run Results](#17-live-run-results)

---

## 1. What the Scraper Does

The scraper automates extraction of every buyer lead from the **IndiaMART Lead Manager → Contacts tab** for the seller account of Aural Essence Private Limited.

For each contact it collects:

| Field | Where It Comes From |
|---|---|
| Buyer Name | View More modal — header |
| Phone Number | View More modal (cleaned, formatted +91XXXXXXXXXX) |
| City | Parsed from location string |
| State | Parsed from location string |
| Full Address | View More → Contact Details section |
| WhatsApp Active | View More modal badge |
| Product Enquired | Enquiry card in the chat panel |
| Quantity | Enquiry card — "Quantity : X" |
| Grade Standard | Enquiry card — "Grade Standard : X" |
| Buyer Filled Details | Enquiry card — e.g. "Needs for hair growth" |
| Probable Order Value | Enquiry card — e.g. "Rs 3,000 to 10,000" |
| Requirement Type | Enquiry card — Business/Personal Use |
| Packaging Type | Enquiry card |
| Chat Messages | Full message thread in right panel |
| Requirements Count | View More → Engagement section |
| Calls Count | View More → Engagement section |
| Replies Count | View More → Engagement section |
| Platform Period | View More — "This Month on platform" |
| Source | Chat — "Contact added through BuyLead / Enquiry" |
| Enquiry Date | Contact card timestamp |
| Scraped At | UTC timestamp of when row was collected |

---

## 2. Tech Stack

| Layer | Tool | Purpose |
|---|---|---|
| **Language** | Python 3.11 | Core scripting language |
| **Browser Automation** | Playwright (sync API) | Controls Chrome, injects JS, reads DOM |
| **Browser** | Google Chrome (stable channel) | Renders the IndiaMART React SPA, holds login session |
| **Connection Method** | Chrome DevTools Protocol (CDP) | Connects Playwright to an already-open Chrome without disturbing the session |
| **Excel Output** | openpyxl | Creates formatted `.xlsx` with headers, freeze panes, auto-filter |
| **CSV Output** | csv (Python stdlib) | UTF-8 BOM CSV, opens cleanly in Excel |
| **Resume Storage** | JSON (Python stdlib) | Checkpoint file — survives crashes |
| **Phone Parsing** | re (Python stdlib) | Regex extraction and formatting of mobile numbers |
| **Polite Delays** | time + random (stdlib) | Randomised waits between actions |

---

## 3. System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        scrape_leads.py                          │
│                                                                 │
│  ┌───────────────┐      ┌──────────────────┐                    │
│  │ checkpoint    │      │  Playwright       │                    │
│  │ .json         │◄────►│  Sync Controller  │                    │
│  │ (resume file) │      └────────┬─────────┘                    │
│  └───────────────┘               │  page.evaluate(JS)           │
│                                  │  page.locator()              │
│  ┌───────────────┐               ▼                              │
│  │ Output Writer │      ┌──────────────────┐                    │
│  │               │      │  Google Chrome   │                    │
│  │ .xlsx (Excel) │◄─────│  (port 9222 CDP) │                    │
│  │ .csv          │      └────────┬─────────┘                    │
│  └───────────────┘               │  real browser session        │
│                                  ▼                              │
│                       ┌──────────────────────┐                  │
│                       │  IndiaMART Lead Mgr  │                  │
│                       │  React SPA           │                  │
│                       │  seller.indiamart    │                  │
│                       │  .com/messagecentre  │                  │
│                       └──────────────────────┘                  │
└─────────────────────────────────────────────────────────────────┘
```

**Data flow in plain English:**

> Playwright tells Chrome what to do → Chrome loads the real IndiaMART page (logged in as Omkar) → JS injected by Playwright reads the DOM → extracted data flows back to Python → Python cleans it → writes to Excel + CSV + checkpoint.

---

## 4. The Core Problem — Why Normal Scraping Fails

There are three reasons a normal HTTP scraper cannot work on IndiaMART Lead Manager:

### Problem 1 — Login Required
The Lead Manager is completely behind authentication. A plain `requests.get()` call returns a login redirect page, not any contact data.

### Problem 2 — React SPA (No Server-Side HTML)
IndiaMART is built with React. The actual contact data is **not in the HTML source** — it is loaded dynamically by JavaScript after the page renders. A `requests` call gets only an empty shell:

```html
<div id="root"></div>  ← all data loads here AFTER JS runs
```

### Problem 3 — ReactVirtualized (Virtual Scrolling)
Even inside the logged-in browser, IndiaMART uses **ReactVirtualized**, which only renders the contacts currently visible on screen. 2,000 contacts in the list → only ~4 DOM elements exist at any time. You cannot simply find all contact elements — most don't exist in the DOM yet.

**Solution to all three**: Use a real logged-in Chrome browser controlled via Playwright + CDP, with custom scroll logic to force-load all contacts before scraping.

---

## 5. How Chrome Connection Works (CDP)

### What is CDP?
Chrome DevTools Protocol is the same protocol your browser's F12 DevTools uses internally. When Chrome is launched with `--remote-debugging-port=9222`, it opens an HTTP endpoint that allows any external program to send commands to the browser.

### Step 1 — Launch Chrome with Debug Port (done once)

```
start_chrome_debug.bat runs:

chrome.exe
  --remote-debugging-port=9222
  --user-data-dir=C:\Users\Dell\IndiaMART_Profile
  --no-first-run
  --disable-blink-features=AutomationControlled
```

The `--user-data-dir` flag points to a **saved Chrome profile folder** that stores:
- Login cookies for IndiaMART
- Session tokens
- Browser history and settings

This means once you log in manually the first time, every future run is **already logged in** automatically.

### Step 2 — Playwright Connects to the Running Chrome

```python
browser = pw.chromium.connect_over_cdp("http://localhost:9222")
page = browser.contexts[0].pages[0]
```

This connects to the **already-open** Chrome window. No new window opens. The existing page, login session, and filter settings are all preserved exactly as the user left them.

### Why Not Use a Fresh Browser?
A fresh browser would need to log in (OTP flow), would have no saved state, and any filter the user applied would be lost. The CDP approach reuses the human's own browser session — completely seamless.

---

## 6. The ReactVirtualized Problem & Solution

### How ReactVirtualized Works
ReactVirtualized renders only the rows currently visible on screen. Each row is absolutely positioned using CSS `top`:

```html
<div class="ReactVirtualized__Grid__innerScrollContainer" style="height: 246000px">
  <!-- Only ~4 divs exist here at any time -->
  <div class="row" style="top: 0px;   height: 120px"> contact 0 </div>
  <div class="row" style="top: 120px; height: 120px"> contact 1 </div>
  <div class="row" style="top: 240px; height: 120px"> contact 2 </div>
  <div class="row" style="top: 360px; height: 120px"> contact 3 </div>
  <!-- contacts 4 to 2049 do NOT exist in DOM yet -->
</div>
```

The `innerScrollContainer` height is `totalContacts × rowHeight` (e.g. 2050 × 120 = 246,000px). This is how the scroll bar appears proportionally correct even though most rows are not rendered.

### Phase 1 — Pre-Scroll to Force-Load All Contacts

Before scraping, the script repeatedly scrolls to the very bottom and waits for new contacts to load:

```python
while stable_count < 6:
    # Jump to bottom — forces IndiaMART to fetch next batch from server
    page.evaluate("list.scrollTop = list.scrollHeight + 99999")
    time.sleep(1.2)

    new_total = scrollHeight / rowHeight  # compute count from geometry
    if new_total > prev_total:
        stable_count = 0   # new contacts appeared — keep scrolling
    else:
        stable_count += 1  # nothing new — count up to 6 then stop
```

Real output from a live run:
```
Step  1:  1541 contacts loaded (+1541)
Step  2:  1552 contacts loaded (+11)
Step  6:  1597 contacts loaded (+24)
Step 10:  1645 contacts loaded (+24)
...
Step 42:  2050 contacts loaded (+1)
All contacts loaded: 2050 total
```

Then scroll back to top before scraping begins.

### Phase 2 — Per-Lead Scroll & Click

For each lead index `idx`, the script:

1. Scrolls the list to bring card `idx` into render range:
```javascript
list.scrollTop = idx * rowHeight;  // e.g. idx=500 → scrollTop=60000
```

2. Finds the rendered DOM row by matching its CSS `top` value:
```javascript
rows.forEach(row => {
    var top = parseFloat(row.style.top);
    if (Math.round(top / rowH) === idx) {
        row.querySelector('[id^="contact-"]').click();
    }
});
```

---

## 7. Complete Scraping Flow A–Z

```
┌─────────────────────────────────────────────────────────┐
│  STARTUP                                                │
│                                                         │
│  1. Load checkpoint.json                                │
│     → Set of already-done indices                       │
│     → Count of saved leads (e.g. 1385)                  │
│                                                         │
│  2. Connect to Chrome via CDP (port 9222)               │
│     → If Chrome open: connect to existing session       │
│     → If Chrome closed: launch with saved profile       │
│                                                         │
│  3. Navigate to seller.indiamart.com/messagecentre      │
│     → If already there: keep filter as-is              │
│     → If login needed: poll every 5s for 3 min          │
│                                                         │
│  4. Click "Contacts" tab                                │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│  PHASE 1 — LOAD ALL CONTACTS                            │
│                                                         │
│  Scroll loop (repeats until 6 stable steps):            │
│    list.scrollTop = list.scrollHeight + 99999           │
│    wait 1.2s                                            │
│    measure: total = scrollHeight / rowHeight            │
│    if total grew → reset stable counter                 │
│    if total same → increment stable counter             │
│                                                         │
│  Scroll back to top                                     │
│  Result: e.g. 2050 total contacts discovered            │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│  PHASE 2 — SCRAPE EACH CONTACT (idx = 0 to 2049)       │
│                                                         │
│  For each idx:                                          │
│                                                         │
│  A. SKIP if idx already in checkpoint                   │
│                                                         │
│  B. Scroll card into viewport                           │
│     list.scrollTop = idx × rowHeight                    │
│                                                         │
│  C. Read card info from DOM                             │
│     name, timestamp, location, message preview          │
│                                                         │
│  D. Click the contact card                              │
│     Right panel loads with full chat thread             │
│                                                         │
│  E. Extract right panel (JS injection)                  │
│     Phone, Product, Quantity, Grade, Order Value,       │
│     Requirement Type, Chat messages, Source             │
│                                                         │
│  F. Find & click "View More" text link                  │
│     Searches all elements for exact text "View More"    │
│     Must be visible on right side (x > 400px)           │
│                                                         │
│  G. Scrape View More modal                              │
│     Buyer name, Phone, Location, WhatsApp status,       │
│     Engagement counts, Address                          │
│                                                         │
│  H. Close modal (× button or Escape key)                │
│                                                         │
│  I. Build final lead record                             │
│     Merge: card info + panel data + modal data          │
│     Phone: modal > panel regex, filter seller number    │
│     Format: +91XXXXXXXXXX                               │
│                                                         │
│  J. Every 5 leads: auto-save                            │
│     checkpoint.json + CSV + Excel                       │
│                                                         │
│  K. Random polite delay (1.0 to 1.6 seconds)            │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│  FINAL SAVE                                             │
│  checkpoint.json + CSV + Excel                          │
│  Print summary: total, phone count, email count         │
└─────────────────────────────────────────────────────────┘
```

---

## 8. JavaScript Injection — The Extraction Engine

Because IndiaMART is a React SPA, all data lives in the JavaScript-rendered DOM. Playwright's `page.evaluate()` runs JavaScript directly inside the live browser page and returns the result to Python.

| JS Function | What It Does |
|---|---|
| `JS_LIST_INFO` | Reads `scrollHeight ÷ rowHeight` to compute total contact count |
| `JS_SCROLL_TO` | Sets `list.scrollTop = idx × rowH` to position a card into the render window |
| `JS_GET_CARD` | Finds the rendered DOM row at absolute index `idx` by matching CSS `top` value; returns name, time, location, preview |
| `JS_CLICK_CARD` | Clicks the contact `<div>` at index `idx` |
| `JS_SCRAPE_PANEL` | Reads `document.body.innerText` and extracts phone numbers, product details, quantities, grades, messages using regex |
| `JS_FIND_CLICK_VIEWMORE` | Iterates all DOM elements for exact text `"View More"` on right side (x > 400px), then clicks it |
| `JS_SCRAPE_MODAL` | Locates the popup by `role="dialog"` or highest z-index, then extracts all buyer details |
| `JS_CLOSE_MODAL` | Clicks the × button found by `aria-label="Close"` or × character text |

---

## 9. View More Modal — Deep Extraction

The **View More** popup is the richest data source. It appears when you click "View More" at the top-right of the chat panel.

```
┌────────────────────────────────────────────────┐
│  C   Chanchal        last seen today 06:36 PM  │  ← Buyer name
│      This Month on platform                    │  ← Platform period
│                                                │
│  📞 06366568964   📍 Bengaluru, Karnataka, India│  ← Phone + Location
│                                                │
│  Chanchal is active on WhatsApp ✓              │  ← WhatsApp status
│                                                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐        │
│  │    0     │ │    0     │ │    0     │        │  ← Engagement stats
│  │REQUIREM. │ │  CALLS   │ │ REPLIES  │        │
│  └──────────┘ └──────────┘ └──────────┘        │
│                                                │
│  Contact Details                               │
│  ADDRESS: Bengaluru, Karnataka, India          │  ← Address
└────────────────────────────────────────────────┘
```

The modal is found in JS by `role="dialog"`. If that fails, the script finds the highest z-index visible overlay — the popup always sits on top of everything else on the page.

---

## 10. Phone Number Processing

Phone data from IndiaMART can be messy. The `clean_phone()` function normalises everything:

```python
SELLER_PHONES = {"6361499510", "8047768879"}  # Omkar's numbers — excluded

def clean_phone(raw):
    digits = re.sub(r"[^\d]", "", raw)         # strip all non-digits
    if len(digits) == 12 and digits[:2] == "91":
        digits = digits[2:]                    # +919876543210 → 9876543210
    if len(digits) == 11 and digits[0] == "0":
        digits = digits[1:]                    # 09876543210 → 9876543210
    if len(digits) == 10 and digits[0] in "6789":
        if digits not in SELLER_PHONES:
            return f"+91{digits}"              # valid Indian mobile ✓
    return ""                                  # reject invalid or seller number
```

**Priority order**: View More modal phone → body text regex → empty string

---

## 11. Checkpoint & Resume System

Every 5 leads, three files are saved:

```
output/
├── checkpoint.json        ← full array of all scraped lead dicts
├── indiamart_leads.xlsx   ← Excel (PermissionError → timestamped fallback)
└── indiamart_leads.csv    ← CSV  (PermissionError → timestamped fallback)
```

On restart, checkpoint loads and already-done indices are skipped:
```python
all_leads = load_ckpt()
done_idxs = {l["_idx"] for l in all_leads}

for idx in range(total):
    if idx in done_idxs:
        continue    # already scraped — skip
```

**File lock protection** — if Excel or CSV is open, saves with a timestamp suffix:
```
indiamart_leads_20261022_142001.xlsx
indiamart_leads_20261022_142001.csv
```

The scraper **never loses data** regardless of crashes or file locks.

---

## 12. Output Files & Formatting

### Excel (`indiamart_leads.xlsx`)

| Feature | Detail |
|---|---|
| Header row | Dark navy blue `#1F4E79` background, white bold text |
| Freeze panes | Row 1 frozen — headers always visible |
| Auto-filter | Every column filterable via dropdown |
| Phone column | Formatted as text — no scientific notation |
| Message column | Word-wrapped, 60px wide, 28px row height |
| Summary sheet | Total / With phone / With email / Scraped at |

### CSV (`indiamart_leads.csv`)
- **UTF-8 with BOM** encoding — opens without garbled characters in Excel on Windows
- 22 fields per row, one lead per row

### 22 Output Columns
```
buyer_name, phone, email, city, state, address,
whatsapp_active, product, quantity, grade,
buyer_filled, order_value, req_type, packaging,
message, req_count, calls_count, replies_count,
platform_period, source, enquiry_date, scraped_at
```

---

## 13. Anti-Detection Measures

| Measure | How It Is Applied | Why It Matters |
|---|---|---|
| Remove automation flag | `ignore_default_args=["--enable-automation"]` | Playwright adds this by default; IndiaMART detects it |
| Hide webdriver property | `navigator.webdriver = undefined` via `add_init_script` | Sites check `window.navigator.webdriver` in JS to detect bots |
| Disable AutomationControlled | `--disable-blink-features=AutomationControlled` | Chrome flag that exposes automation to websites |
| Use real Chrome | `channel="chrome"` | Real Chrome, not Playwright's bundled Chromium |
| Real saved profile | `user_data_dir=C:\Users\Dell\IndiaMART_Profile` | Real cookies, real fingerprint, real session |
| Randomised delays | `time.sleep(1.0 + random.uniform(0, 0.6))` | Human-like irregular timing between actions |

---

## 14. File & Folder Structure

```
d:\AURAL ESSENCE PRIVATE LIMITED\
└── scrapeleads\
    │
    ├── scrape_leads.py            ← Main scraper — entire logic
    ├── start_chrome_debug.bat     ← Launches Chrome with --remote-debugging-port=9222
    ├── SCRAPER_DOCUMENTATION.md   ← This document
    │
    ├── diagnose.py                ← Dev tool: dumps DOM structure to JSON
    ├── check_count.py             ← Dev tool: checks contact count in list
    │
    └── output\
        ├── indiamart_leads.xlsx   ← Final Excel output (primary deliverable)
        ├── indiamart_leads.csv    ← Final CSV output
        └── checkpoint.json        ← Resume state (JSON array of all leads)

C:\Users\Dell\IndiaMART_Profile\
    └── (Chrome saved session — IndiaMART login persists here)
```

---

## 15. How to Run

### One-Time Setup
```bash
pip install playwright openpyxl
playwright install chrome
```

### Every Run

**Step 1 — Open Chrome with debug port**
```
Double-click: scrapeleads\start_chrome_debug.bat
```
Chrome opens using the saved IndiaMART login session.
Navigate to `seller.indiamart.com/messagecentre` → Contacts tab.

**Step 2 — Run the scraper**
```bash
python scrapeleads/scrape_leads.py
```

The scraper will:
1. Connect to your open Chrome automatically
2. Print checkpoint count (how many leads already saved)
3. Scroll to load ALL contacts (can be 1000–2000+)
4. Scrape each one — printing a live row per lead
5. Auto-save every 5 leads
6. Print a final summary on completion

**To resume after a crash**: just run Step 2 again — checkpoint handles the rest automatically.

---

## 16. Ethical Constraints

| Constraint | How It Is Handled |
|---|---|
| No CAPTCHA bypass | Logged-in session used — IndiaMART never triggers CAPTCHA for authenticated sellers |
| No login automation | User logs in manually once; session saved to disk permanently |
| No proxy rotation | Not needed — legitimate registered seller account |
| No invented data | All fields are `""` if not found. Nothing is guessed or fabricated |
| No "View Number" reveal | Phone comes from the View More modal which shows numbers to logged-in sellers |
| Polite delays | 1.0–1.6 second randomised sleep between every lead |
| Contacts tab only | Strictly limited to Lead Manager → Contacts. BuyLeads and Calls tabs are not touched |

---

## 17. Live Run Results

From the actual scraping run in October 2026:

| Metric | Value |
|---|---|
| Total contacts discovered | **2,050** |
| Leads scraped (ongoing) | **1,370+** |
| Phone numbers captured | **~99%** of all scraped leads |
| View More modal success | **100%** — vm=✓ on every single lead |
| Crash recoveries | 3 — all resumed from checkpoint with zero data loss |
| Average time per lead | ~5 seconds |
| Estimated total run time | ~3 hours for all 2,050 leads |
| Output file | `scrapeleads/output/indiamart_leads.xlsx` |

Sample rows from the output:

| Buyer | Phone | City | Product | Order Value |
|---|---|---|---|---|
| Chanchal | +916366568964 | Bengaluru | Natural Rosemary Oil | — |
| Vishnu Kumar Raval | +918618700126 | Bengaluru | Rose Water | — |
| PANDURANG | +919637267111 | Solapur | 100% Natural Rose Water | Rs 3,000–10,000 |
| Deepak Kashikar | +919834154836 | — | Dabur Gulabari Rose Water | — |
| Shadan Ahmad | +919623558016 | — | Pure Rose Water | — |
| Samir Maji | +917795373443 | — | Tekka Rose Water | — |
