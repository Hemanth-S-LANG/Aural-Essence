"""
fetch_phones.py — Main contact enrichment runner.

Pipeline per seller:
  Step 1 — Gemini (with web search grounding): finds phone/email by searching online
  Step 2 — If Gemini returns a URL but no phone: visit that URL and scrape Contact page
  Step 3 — Google Maps via Playwright: reads publicly listed business phone
  Step 4 — Grok fallback: same as Gemini but different AI model

Usage:
  python fetch_contact/fetch_phones.py --file output/rose_water_complete_final.xlsx
  python fetch_contact/fetch_phones.py --all
  python fetch_contact/fetch_phones.py --file output/rose_water_complete_final.xlsx --limit 50
"""
import argparse, json, sys, time, random
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fetch_contact.scrapers.gemini_search  import search_contact_gemini
from fetch_contact.scrapers.grok_search    import search_contact_grok
from fetch_contact.scrapers.google_maps    import search_google_maps
from fetch_contact.scrapers.google_search  import search_seller_contact, close_browser
from fetch_contact.utils.excel_updater     import load_sellers, batch_write_results
from fetch_contact.utils.phone_extractor   import extract_phones, extract_emails

OUTPUT_DIR  = Path(__file__).parent.parent / "output"
CKPT_DIR    = Path(__file__).parent.parent / "data"
CKPT_DIR.mkdir(exist_ok=True)

DELAY       = 4.0    # minimum seconds between API/browser calls
CKPT_EVERY  = 10     # save checkpoint every N sellers (was 25)


def get_checkpoint_path(xlsx_path: Path) -> Path:
    return CKPT_DIR / f"fetch_contact_{xlsx_path.stem}.json"


def load_checkpoint(xlsx_path: Path) -> dict:
    cp = get_checkpoint_path(xlsx_path)
    if cp.exists():
        try: return json.loads(cp.read_text(encoding="utf-8"))
        except Exception: return {}
    return {}


def save_checkpoint(xlsx_path: Path, data: dict) -> None:
    get_checkpoint_path(xlsx_path).write_text(json.dumps(data), encoding="utf-8")


def visit_url_for_phone(url: str) -> dict:
    """Visit a URL and extract phone/email from the page + its Contact page."""
    if not url or not url.startswith("http"):
        return {"phone": None, "email": None}
    try:
        import requests as req
        from bs4 import BeautifulSoup
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                 "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                   "Accept": "text/html,*/*;q=0.8",
                   "Accept-Language": "en-IN,en;q=0.9"}
        r = req.get(url, headers=headers, timeout=15, allow_redirects=True)
        if r.status_code != 200:
            return {"phone": None, "email": None}

        soup = BeautifulSoup(r.text, "lxml")
        text = soup.get_text(" ", strip=True)

        # Try to find and follow Contact/About link
        contact_url = None
        for a in soup.find_all("a", href=True):
            href = a.get("href","").lower()
            txt  = a.get_text(strip=True).lower()
            if any(kw in href or kw in txt for kw in
                   ["contact","about","reach","enquiry","inquiry","touch"]):
                full = a["href"]
                if full.startswith("/"):
                    from urllib.parse import urljoin
                    full = urljoin(url, full)
                if full.startswith("http") and full != url:
                    contact_url = full; break

        phones = extract_phones(text)
        emails = extract_emails(text)

        if not phones and contact_url:
            time.sleep(2)
            try:
                cr = req.get(contact_url, headers=headers, timeout=12)
                ct = BeautifulSoup(cr.text,"lxml").get_text(" ",strip=True)
                phones = extract_phones(ct) or phones
                emails = extract_emails(ct) or emails
            except Exception: pass

        return {"phone": phones[0] if phones else None,
                "email": emails[0] if emails else None,
                "source_url": contact_url or url}
    except Exception as e:
        return {"phone": None, "email": None, "error": str(e)}


def process_seller(seller: dict) -> dict:
    """
    Pure browser pipeline — exactly like a human searching on Google:

      Step 1: Search "Company City contact details" on Google
              → Read AI Overview (phone right there most of the time)
      Step 2: Click AI Mode tab on Google
              → Richer AI answer with more details
      Step 3: Visit top result pages (JustDial, Sulekha, company website)
      Step 4: Google Maps as final fallback
    """
    company = str(seller.get("company_name") or "").strip()
    city    = str(seller.get("city")         or "").strip()
    state   = str(seller.get("state")        or "").strip()
    address = str(seller.get("address")      or "").strip()

    if not company:
        return {"phone": None, "email": None, "phone_source": "",
                "source_url": "", "notes": "No company name"}

    location = city or state
    print(f"    {company[:50]:<50} | {location}")

    # ── Step 1+2+3: Google browser search ────────────────────────────────────
    # Randomised delay — 4 to 7 seconds between searches to avoid CAPTCHA
    time.sleep(DELAY + random.uniform(0, 3))
    result = search_seller_contact(company, city, state, address, delay=2.0)
    if result.get("phone"):
        src = result.get("phone_source", "GOOGLE")
        print(f"      ✓ {src}: {result['phone']} | {result.get('notes','')[:60]}")
        return result

    # ── Step 4: Google Maps fallback ──────────────────────────────────────────
    time.sleep(DELAY)
    maps = search_google_maps(company, location, delay=3.0)
    if maps.get("phone"):
        print(f"      ✓ MAPS: {maps['phone']} | {maps.get('notes','')[:55]}")
        return maps

    # Maps found a website — visit it
    maps_website = maps.get("website_url", "")
    if maps_website:
        time.sleep(DELAY)
        pg = visit_url_for_phone(maps_website)
        if pg.get("phone"):
            print(f"      ✓ WEBSITE (via Maps): {pg['phone']}")
            return {"phone": pg["phone"], "email": pg.get("email"),
                    "phone_source": "WEBSITE",
                    "source_url": pg.get("source_url", maps_website),
                    "notes": f"From Maps website: {maps_website[:60]}"}

    print(f"      — not found")
    return {"phone": None, "email": None,
            "phone_source": "", "source_url": "",
            "notes": "Not found via Google or Maps"}


def process_file(xlsx_path: Path, missing_only: bool = True,
                 limit: int = 0) -> None:
    print(f"\n{'='*70}")
    print(f"Processing: {xlsx_path.name}")
    print(f"{'='*70}")

    sellers, col_idx = load_sellers(xlsx_path, missing_only=missing_only)
    if not sellers:
        print("  No sellers to process."); return

    checkpoint = load_checkpoint(xlsx_path)
    done_rows  = set(int(k) for k in checkpoint.keys())
    todo       = [s for s in sellers if s["_row"] not in done_rows]
    if limit:
        todo = todo[:limit]

    print(f"  Total in file    : {len(sellers)}")
    print(f"  Already done     : {len(done_rows)}")
    print(f"  To process now   : {len(todo)}")
    if limit:
        print(f"  (Capped at {limit} for this run)")

    if not todo:
        print("  Nothing new to process — writing final Excel...")
        batch_write_results(xlsx_path, {int(k): v for k,v in checkpoint.items()})
        _print_summary(checkpoint)
        return

    results     = dict(checkpoint)
    found_count = sum(1 for v in results.values() if v.get("phone"))
    start       = datetime.utcnow()

    for i, seller in enumerate(todo, 1):
        row_num = seller["_row"]
        print(f"\n  [{i:4d}/{len(todo)}] Row {row_num}")

        result = process_seller(seller)
        results[str(row_num)] = result
        if result.get("phone"): found_count += 1

        if i % 10 == 0:
            elapsed = (datetime.utcnow() - start).total_seconds()
            rate    = elapsed / i
            eta_min = rate * (len(todo) - i) / 60
            pct     = found_count * 100 // (len(done_rows) + i)
            print(f"\n  ── {i}/{len(todo)} | Found: {found_count} ({pct}%) | ETA: {eta_min:.0f} min ──\n")

        if i % CKPT_EVERY == 0:
            save_checkpoint(xlsx_path, results)
            batch_write_results(xlsx_path, {int(k): v for k,v in results.items()})
            print(f"  ✓ Checkpoint saved ({i} done, {found_count} phones found so far)")

    save_checkpoint(xlsx_path, results)
    out = batch_write_results(xlsx_path, {int(k): v for k,v in results.items()})
    _print_summary(results, xlsx_path=out)
    close_browser()  # shut down Playwright browser cleanly


def _print_summary(results: dict, xlsx_path: Path = None) -> None:
    total  = len(results)
    found  = sum(1 for v in results.values() if v.get("phone"))
    srcs   = {}
    for v in results.values():
        s = v.get("phone_source","")
        if s: srcs[s] = srcs.get(s,0) + 1
    print(f"\n{'='*55}")
    print(f"  SUMMARY")
    print(f"{'='*55}")
    print(f"  Processed  : {total}")
    print(f"  Phones found: {found} ({found*100//total if total else 0}%)")
    print(f"  By source:")
    for s, n in sorted(srcs.items(), key=lambda x: -x[1]):
        print(f"    {s:<22}: {n}")
    if xlsx_path:
        print(f"  Output: {xlsx_path}")
    print(f"{'='*55}\n")


def main():
    parser = argparse.ArgumentParser(
        prog="python fetch_contact/fetch_phones.py",
        description="Enrich Excel seller data with phone numbers"
    )
    parser.add_argument("--file", metavar="PATH", help="Specific Excel file")
    parser.add_argument("--all",  action="store_true", help="All files in output/")
    parser.add_argument("--include-done", action="store_true",
                        help="Re-process rows that already have a phone")
    parser.add_argument("--limit", type=int, default=0,
                        help="Process only N sellers per file (0=all)")
    args = parser.parse_args()

    missing_only = not args.include_done

    if args.file:
        xlsx = Path(args.file)
        if not xlsx.exists(): print(f"File not found: {xlsx}"); sys.exit(1)
        process_file(xlsx, missing_only=missing_only, limit=args.limit)
    else:
        xlsx_files = sorted(f for f in OUTPUT_DIR.glob("*.xlsx")
                            if not f.name.startswith("~$"))
        if not xlsx_files: print(f"No .xlsx files in {OUTPUT_DIR}"); sys.exit(1)
        print(f"Found {len(xlsx_files)} Excel files:")
        for f in xlsx_files: print(f"  {f.name}")
        for xlsx in xlsx_files:
            process_file(xlsx, missing_only=missing_only, limit=args.limit)
        print("\nAll files processed.")


if __name__ == "__main__":
    main()
