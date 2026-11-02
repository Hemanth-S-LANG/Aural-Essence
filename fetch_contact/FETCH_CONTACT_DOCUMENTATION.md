# fetch_contact — Complete Technical Documentation

**Company**: Aural Essence Private Limited  
**Module**: Phone & Email Enrichment Pipeline  
**Purpose**: Automatically find phone numbers and emails for 1,068 rose water sellers from TradeIndia, using Google AI Overview as the primary source  

---

## Table of Contents

1. [What It Does](#1-what-it-does)
2. [Tech Stack](#2-tech-stack)
3. [Folder Structure](#3-folder-structure)
4. [System Architecture](#4-system-architecture)
5. [Complete A–Z Flow](#5-complete-az-flow)
6. [Each File Explained](#6-each-file-explained)
7. [The 4-Step Search Pipeline](#7-the-4-step-search-pipeline)
8. [Google Search — How It Works](#8-google-search--how-it-works)
9. [Phone Extraction — How It Works](#9-phone-extraction--how-it-works)
10. [Excel Read & Write](#10-excel-read--write)
11. [Checkpoint System](#11-checkpoint-system)
12. [Throttle & CAPTCHA Handling](#12-throttle--captcha-handling)
13. [How to Run](#13-how-to-run)
14. [Live Results](#14-live-results)

---

## 1. What It Does

The rose water scraper collects 1,068 seller companies from TradeIndia — but TradeIndia hides all phone numbers behind a login wall. The `fetch_contact` module finds those phone numbers from **public sources** on the internet.

**Input**: `output/rose_water_complete_final.xlsx` — 1,068 rows with company name, city, state  
**Output**: Same Excel file with new columns filled in — Phone Number, Email, Source, Notes

**Fields it enriches per seller:**

| Column | Example |
|---|---|
| Phone Number | +919876543210 |
| Email | contact@company.com |
| Phone Status | VALID / HIDDEN |
| Email Status | VALID / MISSING |
| Phone Source | GOOGLE_AI_OVERVIEW / WEBSITE / GOOGLE_MAPS |
| Source URL | https://justdial.com/... |
| Fetch Notes | AI Overview: Vijaylaxmi Fragrances Kannauj... |
| Fetched At | 2026-10-29 05:39 UTC |

---

## 2. Tech Stack

| Layer | Tool | Version | Purpose |
|---|---|---|---|
| Language | Python | 3.11 | Core scripting |
| Browser Automation | Playwright (sync) | 1.40+ | Controls Chrome for Google search |
| Browser | Google Chrome | stable | Executes searches with real cookies |
| Excel Read/Write | openpyxl | 3.1+ | Reads seller list, writes back phone data |
| Phone Validation | phonenumbers | 8.13+ | Validates Indian numbers to E.164 format |
| HTTP Requests | requests | 2.31+ | Visits company websites to scrape phone |
| HTML Parsing | BeautifulSoup4 (lxml) | 4.12+ | Parses website pages |
| AI Fallback (optional) | Gemini API | gemini-3.8-flash | AI-powered search (when quota available) |
| AI Fallback (optional) | Grok API (xAI) | grok-beta | Second AI fallback |
| Environment Config | python-dotenv | 1.0+ | Loads API keys from .env file |

---

## 3. Folder Structure

```
fetch_contact/
│
├── fetch_phones.py          ← MAIN ENTRY POINT — run this
│
├── scrapers/
│   ├── google_search.py     ← Step 1: Google AI Overview (Playwright)
│   ├── google_maps.py       ← Step 2: Google Maps business panel
│   ├── gemini_search.py     ← Step 3: Gemini AI with web search
│   ├── grok_search.py       ← Step 4: Grok AI fallback
│   └── ai_fallback.py       ← Generic AI fallback helpers
│
├── utils/
│   ├── phone_extractor.py   ← Regex + phonenumbers validation
│   └── excel_updater.py     ← Read sellers from Excel, write results back
│
├── .env                     ← API keys (NOT in GitHub)
├── .env.example             ← Template showing which keys are needed
└── README.md                ← Quick usage guide
```

---

## 4. System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        fetch_phones.py                              │
│                     (Orchestrator / Main)                           │
│                                                                     │
│  1. Load checkpoint (skip already-found sellers)                    │
│  2. Load sellers from Excel (openpyxl)                              │
│  3. For each seller → call process_seller()                         │
│  4. Save results back to Excel every 10 rows                        │
└────────────────────────┬────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   process_seller()                                  │
│              4-Step Pipeline (highest yield first)                  │
│                                                                     │
│  Step 1: google_search.py                                           │
│    → Open Chrome tab (CDP port 9222)                                │
│    → Search: "Company City contact details"                         │
│    → Read AI Overview — phone right there                           │
│    → Throttle detected: wait 30–60s, retry                          │
│    → CAPTCHA detected: alert user, wait for solve                   │
│                                                                     │
│  Step 2: google_maps.py   [if Step 1 failed]                        │
│    → Search Google Maps for the business listing                    │
│    → Read phone from the Maps panel                                 │
│    → Also gets website URL for Step 3                               │
│                                                                     │
│  Step 3: visit website   [if Maps found URL but no phone]           │
│    → GET homepage with requests                                     │
│    → Follow Contact/About link                                      │
│    → Scrape phone from contact page                                 │
│                                                                     │
│  phone_extractor.py  [used by ALL steps]                            │
│    → Regex finds phone patterns                                     │
│    → phonenumbers validates Indian format                           │
│    → Returns E.164: +91XXXXXXXXXX                                   │
│                                                                     │
│  excel_updater.py                                                   │
│    → Reads sellers at startup                                       │
│    → Writes phone/email/source back every 10 rows                   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. Complete A–Z Flow

```
START
  │
  ├─ Load checkpoint from data/fetch_contact_rose_water_complete_final.json
  │    Already-found sellers stored here — SKIPPED on restart
  │
  ├─ Load Excel: output/rose_water_complete_final.xlsx
  │    Reads: Company Name, City, State, Address, GST No
  │    Skips rows that already have Phone Status = VALID
  │
  ├─ Print summary:
  │    Total in file    : 1068
  │    Already done     : 610
  │    To process now   : 458
  │
  └─ FOR EACH seller:
       │
       ├─ Wait 8–15 seconds (randomised — avoids throttling)
       │
       ├─ STEP 1: Google Search
       │    Connect to Chrome via CDP → open new tab
       │    Navigate: google.com/search?q=Company+City+contact+details
       │    Check: CAPTCHA? → wait for user to solve
       │    Check: Throttled (empty page)? → wait 30–60s, retry
       │    Extract from: AI Overview, Knowledge Panel, snippets
       │    FOUND → return phone immediately
       │    NOT FOUND → try visiting top result URLs
       │
       ├─ STEP 2: Google Maps
       │    Headless browser → google.com/maps/search/Company+City
       │    Read [data-item-id*="phone"] elements
       │    FOUND → return phone
       │    Get website URL → for Step 3
       │
       ├─ STEP 3: Visit website
       │    GET homepage → find Contact link → scrape phone
       │    FOUND → return phone
       │
       ├─ Save to checkpoint (every 10 rows)
       └─ Write to Excel (every 10 rows)

END — print total / phones found / by source
```

---

## 6. Each File Explained

### `fetch_phones.py` — The Orchestrator
The only file you run directly. Handles the loop, checkpoint, Excel writing, delays, and backoff logic.

**Key settings:**
```python
DELAY      = 4.0   # minimum seconds between steps
CKPT_EVERY = 10    # save every N sellers
_consec_miss = 0   # tracks consecutive misses for backoff
```

### `scrapers/google_search.py` — Primary Engine
Mimics exactly what you do manually on Google. Uses CDP to connect to your already-open Chrome (which has real Google cookies). Detects both CAPTCHA and silent throttling.

### `scrapers/google_maps.py` — Maps Fallback
Launches a separate headless browser, searches Google Maps, reads the phone from the business panel without clicking anything.

### `scrapers/gemini_search.py` — Gemini AI (Optional)
Uses Gemini API with Google Search grounding. Same query style as Google AI Mode: `"Super Honey Lucknow contact details"`. Extracts phone from free-text AI response.

### `scrapers/grok_search.py` — Grok AI (Optional)
Uses xAI Grok API as a second AI fallback. Structured prompt asking for PHONE/EMAIL/SOURCE in a specific format.

### `utils/phone_extractor.py` — Phone Validator
All found text passes through here. Runs 5 regex patterns, validates with Google's `phonenumbers` library, rejects dummies, returns E.164 format.

### `utils/excel_updater.py` — Excel Handler
Reads sellers from any Excel layout (handles column name variations). Writes results back with colour coding, hyperlinks, text formatting for phone numbers.

---

## 7. The 4-Step Search Pipeline

```
Seller: "Vijaylaxmi Fragrances, Kannauj"
     │
     ▼
STEP 1: Google Search
  Query: "Vijaylaxmi Fragrances Kannauj contact details"
  Result: ✓ +918048204055 from AI Overview → RETURN, stop here
     │ (if not found)
     ▼
STEP 2: Google Maps
  Search: maps.google.com → business panel → phone
  Also get website URL
     │ (if not found but URL found)
     ▼
STEP 3: Visit Website
  GET company website → Contact page → scrape phone
     │ (if all fail)
     ▼
RESULT: "not found" — saved to checkpoint, retried on next run
```

---

## 8. Google Search — How It Works

### Why it doesn't get blocked
1. Real Chrome with real Google cookies (not headless)
2. CDP connects to your already-open window — Google sees a trusted user
3. Randomised delays of 8–15 seconds between searches

### Query format
```
"Super Honey Lucknow contact details"
```
This triggers Google's AI Overview which shows phone + email directly in results.

### What gets extracted
```
AI Overview box         ← phone appears here most of the time
Knowledge Panel         ← right sidebar phone number
Search result snippets  ← "Call us: 9876543210"
Top result pages        ← JustDial, Sulekha, company website
```

---

## 9. Phone Extraction — How It Works

```
Input text: "Contact us at +91 78800-94120"
     │
     ▼  5 regex patterns scan the text
     │
     ▼  Match: "+91 78800-94120" → digits = "917880094120"
     │   strip +91 → "7880094120" (10 digits, starts with 7) ✓
     │   not dummy ✓
     │   phonenumbers.parse("+917880094120") → VALID ✓
     │
Output: "+917880094120"
```

**Rejects:** `9999999999`, `1234567890`, `9898989898`, zero-filled numbers, unvalidated formats.

---

## 10. Excel Read & Write

**Reading:** `load_sellers()` maps column names, skips VALID rows  
**Writing:** `batch_write_results()` writes to exact row numbers, adds 8 new columns  
**Phone format:** Stored as text (`@`) — never becomes scientific notation  
**Colours:** Green = found, Grey = not found  
**Error handling:** If file is open → saves as timestamped copy  

---

## 11. Checkpoint System

**File:** `data/fetch_contact_rose_water_complete_final.json`

```json
{
  "2":  {"phone": "+919157618576", "phone_source": "GOOGLE_AI_OVERVIEW"},
  "5":  {"phone": "+918527849191", "phone_source": "WEBSITE"},
  "17": {"phone": null, "notes": "Not found"}
}
```

Key = Excel row number. Saves every 10 sellers. Fully resume-safe.

---

## 12. Throttle & CAPTCHA Handling

| Type | Detection | Response |
|---|---|---|
| CAPTCHA | `/sorry/` in URL + no DOM results | Alert user, bring Chrome front, wait up to 10 min |
| Silent Throttle | Page loads empty (< 500 chars body) | Auto-wait 30–60s, retry once |
| Consecutive misses | 5+ in a row | Add 40–90s extra backoff |

CAPTCHA solve detection uses DOM check (not just URL) — checks every 10s for real search results to appear in the page.

---

## 13. How to Run

```bash
# Make sure Chrome is open with debug port first
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\Users\Dell\IndiaMART_Profile"

# Standard run
python fetch_contact/fetch_phones.py --file output/rose_water_complete_final.xlsx

# Test with 20 sellers
python fetch_contact/fetch_phones.py --file output/rose_water_complete_final.xlsx --limit 20

# All Excel files
python fetch_contact/fetch_phones.py --all
```

**To reset and retry missing sellers:**
```python
import json
from pathlib import Path
ckpt = Path("data/fetch_contact_rose_water_complete_final.json")
data = json.loads(ckpt.read_text(encoding="utf-8"))
kept = {k: v for k, v in data.items() if isinstance(v, dict) and v.get("phone")}
ckpt.write_text(json.dumps(kept, indent=2), encoding="utf-8")
print(f"Kept {len(kept)}, reset {len(data)-len(kept)} for retry")
```

---

## 14. Live Results

| Metric | Value |
|---|---|
| Total sellers | 1,068 |
| Phones found | 610 (57%) |
| Primary source | GOOGLE_AI_OVERVIEW (~80% of found) |
| Secondary source | WEBSITE (via Maps URL) |
| Tertiary source | GOOGLE_MAPS directly |

**Why some not found:** No online presence, foreign sellers (Sydney/Karachi), large brands (Dabur/Oriflame) with corporate-only numbers.
