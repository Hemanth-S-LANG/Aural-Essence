# fetch_contact — Phone Number Enrichment Tool

Adds phone numbers to your TradeIndia Excel files by searching publicly visible sources.

## Setup

1. Open `fetch_contact/.env` and paste your API keys:
   ```
   GEMINI_API_KEY=your_key_here
   XAI_API_KEY=your_key_here
   ```

2. Install python-dotenv if not already installed:
   ```
   pip install python-dotenv
   ```

## How to run

```bash
# Process all Excel files in output/ folder
python fetch_contact/fetch_phones.py --all

# Process one specific file
python fetch_contact/fetch_phones.py --file output/rose_water_complete_final.xlsx

# Re-process even sellers that already have phones
python fetch_contact/fetch_phones.py --file output/rose_water_complete_final.xlsx --include-done
```

## What it does

For each seller row (where Phone Status = HIDDEN/MISSING):

1. **Google/DuckDuckGo Search** — searches `"Company Name" City contact phone`
   - Checks top 5 results
   - Visits each website's Contact/About page
   - Extracts Indian phone number with regex + phonenumbers library

2. **Google Maps** — searches company name + city on Google Maps
   - Reads publicly listed business phone from the Maps panel
   - No login, no clicking — reads only what's visible

3. **AI Fallback (Gemini → Grok)** — if a webpage was found but phone extraction failed
   - Sends page text to Gemini/Grok
   - Asks AI to extract phone from the text
   - Only used as last resort

## Output

Same Excel file is updated with new columns:
- **Phone Number** — found phone in +91XXXXXXXXXX format
- **Email** — found email if any
- **Phone Source** — WEBSITE / GOOGLE_MAPS / GEMINI / GROK
- **Source URL** — exact webpage where number was found
- **Fetch Notes** — any caveats or uncertainty
- **Fetched At** — timestamp

## Resume after interruption

The tool saves a checkpoint after every 25 sellers.
If it stops for any reason, just run the same command again — it picks up where it left off.

## Expected yield

- ~20–35% of sellers will get a phone number found automatically
- The rest stay as HIDDEN (phone exists but not publicly published anywhere)
