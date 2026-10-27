"""
Google Search scraper — browser automation only. No API keys.

Exactly how a human does it:
  1. Reuse open Chrome via CDP (port 9222) — has real Google cookies
  2. Search: "Company City contact details"
  3. Read AI Overview — phone/email right there most of the time
  4. If CAPTCHA appears — pause and wait for user to solve it manually
  5. If no phone in AI Overview — visit top result pages

CAPTCHA handling:
  When Google shows the CAPTCHA page, the script prints a message
  and waits up to 5 minutes for the user to tick "I am not a robot"
  in the visible Chrome window, then resumes automatically.

Delays are randomised (4–7s per search) to avoid triggering CAPTCHA.
"""
import re, time, random
from typing import Dict, List
from pathlib import Path

from fetch_contact.utils.phone_extractor import extract_phones, extract_emails

SKIP_DOMAINS = {
    "tradeindia.com", "indiamart.com", "amazon.in", "flipkart.com",
    "meesho.com", "myntra.com", "nykaa.com", "facebook.com",
    "instagram.com", "twitter.com", "linkedin.com", "youtube.com",
    "wikipedia.org", "google.com", "gstatic.com",
}

PROFILE_DIR = Path.home() / "IndiaMART_Profile"
PROFILE_DIR.mkdir(exist_ok=True)

_BROWSER = None
_PAGE    = None
_PW      = None


def _get_page():
    global _BROWSER, _PAGE, _PW
    try:
        if _PAGE:
            _PAGE.title()
            return _PAGE
    except Exception:
        _PAGE = None

    from playwright.sync_api import sync_playwright

    # Try CDP to already-open Chrome first (has real Google cookies)
    try:
        pw_tmp = sync_playwright().start()
        browser = pw_tmp.chromium.connect_over_cdp("http://localhost:9222")
        ctx = browser.contexts[0] if browser.contexts else None
        if ctx:
            page = ctx.new_page()
            page.set_default_timeout(25_000)
            _PW = pw_tmp; _BROWSER = browser; _PAGE = page
            return _PAGE
        pw_tmp.stop()
    except Exception:
        try: pw_tmp.stop()
        except Exception: pass

    # Fallback: launch with saved profile
    _PW = sync_playwright().start()
    _BROWSER = _PW.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False, channel="chrome",
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled",
              "--disable-infobars"],
        ignore_default_args=["--enable-automation"],
        no_viewport=True,
    )
    _PAGE = _BROWSER.pages[0] if _BROWSER.pages else _BROWSER.new_page()
    _PAGE.set_default_timeout(25_000)
    _PAGE.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    )
    return _PAGE


def close_browser():
    global _BROWSER, _PAGE, _PW
    try:
        if _PAGE: _PAGE.close()
    except Exception:
        pass
    _PAGE = _BROWSER = _PW = None


# ── CAPTCHA helpers ───────────────────────────────────────────────────────────

def _is_captcha(page) -> bool:
    """Returns True if Google is showing the CAPTCHA/sorry page."""
    try:
        url = page.url or ""
        return "/sorry/" in url or "google.com/sorry" in url
    except Exception:
        return False


def _wait_captcha(page, timeout_min: int = 5) -> bool:
    """
    Print a message and wait up to timeout_min minutes for the user
    to solve the CAPTCHA in the Chrome window.
    Returns True when solved, False on timeout.
    """
    print(f"\n{'!'*60}")
    print("  GOOGLE CAPTCHA — please solve it in the Chrome window")
    print(f"  Waiting up to {timeout_min} minutes...")
    print(f"{'!'*60}")

    for i in range(timeout_min * 12):   # poll every 5 seconds
        time.sleep(5)
        try:
            if not _is_captcha(page):
                print("  Captcha solved — resuming.\n")
                time.sleep(2)
                return True
        except Exception:
            pass
        if i > 0 and i % 12 == 0:
            print(f"  Still waiting... ({timeout_min - i//12} min left)")

    print("  Captcha timeout — skipping this search.\n")
    return False


# ── Main search function ──────────────────────────────────────────────────────

def _search_google(query: str) -> Dict:
    """
    Open Google, search the query, extract phones/emails from:
    - AI Overview (shown at top — has phone most of the time)
    - Knowledge Panel (right sidebar)
    - Search result snippets
    Returns {phones, emails, urls, raw_text}
    """
    result = {"phones": [], "emails": [], "urls": [], "raw_text": ""}
    try:
        page = _get_page()
        encoded = query.replace(" ", "+")
        page.goto(
            f"https://www.google.com/search?q={encoded}&hl=en&gl=in",
            wait_until="domcontentloaded",
            timeout=25_000,
        )
        time.sleep(2.5)

        # Handle CAPTCHA — wait for user to solve it in Chrome window
        if _is_captcha(page):
            solved = _wait_captcha(page, timeout_min=5)
            if not solved:
                return result
            # Re-navigate after solving
            page.goto(
                f"https://www.google.com/search?q={encoded}&hl=en&gl=in",
                wait_until="domcontentloaded",
                timeout=25_000,
            )
            time.sleep(2.5)

        # Accept cookies if prompted
        for btn_text in ["Accept all", "I agree"]:
            try:
                btn = page.locator(f'button:has-text("{btn_text}")').first
                if btn.count() > 0:
                    btn.click(); time.sleep(1); break
            except Exception:
                pass

        full_text = page.evaluate("() => document.body.innerText") or ""
        result["raw_text"] = full_text

        # Extract rich text from AI Overview + Knowledge Panel + snippets
        rich = page.evaluate(r"""
            () => {
                var parts = [];
                var selectors = [
                    '[data-attrid*="overview"]', '[class*="wDYxhc"]',
                    '[class*="ILfuVd"]', '[class*="LGOjhe"]',
                    '[class*="ai-overview"]', '[class*="SGlybb"]',
                    '[jsname="yEVEwb"]', '.xpdopen',
                    '[data-attrid="wa:/description"]',
                    '.ifM9O', '.c2xzTb', '.V3FYCf',
                    '[data-attrid*="phone"]', '[data-attrid*="telephone"]',
                    '.VwiC3b', '.s3v9rd', '.IsZvec', '.aCOpRe',
                    '[data-sncf="1"]'
                ];
                selectors.forEach(function(sel) {
                    document.querySelectorAll(sel).forEach(function(el) {
                        var t = el.innerText.trim();
                        if (t.length > 10) parts.push(t);
                    });
                });
                return parts.join('\n\n');
            }
        """) or ""

        combined = rich + "\n\n" + full_text
        phones = extract_phones(combined)
        emails = extract_emails(combined)
        result["phones"] = list(dict.fromkeys(phones))
        result["emails"] = list(dict.fromkeys(emails))

        # Collect result URLs for fallback page visits
        urls = page.evaluate(r"""
            () => {
                var seen = new Set();
                var links = [];
                document.querySelectorAll('a[href]').forEach(function(a) {
                    var h = a.href;
                    if (h && h.startsWith('http') &&
                        !h.includes('google.com') &&
                        !h.includes('gstatic.com') &&
                        !seen.has(h)) {
                        seen.add(h); links.push(h);
                    }
                });
                return links.slice(0, 15);
            }
        """) or []
        result["urls"] = [u for u in urls
                          if not any(d in u for d in SKIP_DOMAINS)]

    except Exception as ex:
        result["error"] = str(ex)
    return result


def _visit_page(url: str) -> Dict:
    """Visit a URL with requests and scrape phone/email + Contact subpage."""
    import requests as req
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin

    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"),
        "Accept": "text/html,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
    }
    out = {"phone": None, "email": None, "source_url": url}
    try:
        r = req.get(url, headers=headers, timeout=12, allow_redirects=True)
        if r.status_code != 200:
            return out
        soup  = BeautifulSoup(r.text, "lxml")
        text  = soup.get_text(" ", strip=True)
        phones = extract_phones(text)
        emails = extract_emails(text)

        if not phones:
            for a in soup.find_all("a", href=True):
                href = a["href"].lower()
                txt  = a.get_text(strip=True).lower()
                if any(k in href or k in txt
                       for k in ["contact", "about", "reach", "enquiry"]):
                    full = a["href"]
                    if full.startswith("/"):
                        full = urljoin(url, full)
                    if full.startswith("http") and full != url:
                        try:
                            cr = req.get(full, headers=headers, timeout=10)
                            ct = BeautifulSoup(cr.text, "lxml").get_text(" ", strip=True)
                            phones = extract_phones(ct) or phones
                            emails = extract_emails(ct) or emails
                            if phones: out["source_url"] = full
                        except Exception:
                            pass
                        break

        out["phone"] = phones[0] if phones else None
        out["email"] = emails[0] if emails else None
    except Exception:
        pass
    return out


def search_seller_contact(company: str, city: str, state: str = "",
                          address: str = "", delay: float = 2.0) -> Dict:
    """
    Search Google for a seller's contact details — pure browser, no API.
    Uses randomised delays (4–7s) to avoid CAPTCHA triggering.
    """
    location = city.strip() or state.strip()

    queries = []
    if location:
        queries.append(f"{company} {location} contact details")
    if state.strip() and state.strip() != location:
        queries.append(f"{company} {state.strip()} contact details")
    queries.append(f"{company} contact phone India")

    for qi, query in enumerate(queries[:2]):
        if qi > 0:
            # Longer delay between query retries
            time.sleep(4 + random.uniform(1, 3))

        sr = _search_google(query)

        if sr.get("phones"):
            return {
                "phone":        sr["phones"][0],
                "email":        sr["emails"][0] if sr.get("emails") else None,
                "phone_source": "GOOGLE_AI_OVERVIEW",
                "source_url":   sr["urls"][0] if sr.get("urls") else "",
                "notes":        f"AI Overview: {query[:70]}",
            }

        # Visit top result pages as fallback
        for url in sr.get("urls", [])[:4]:
            time.sleep(1.5 + random.uniform(0, 1))
            pg = _visit_page(url)
            if pg.get("phone"):
                return {
                    "phone":        pg["phone"],
                    "email":        pg.get("email"),
                    "phone_source": "WEBSITE",
                    "source_url":   pg["source_url"],
                    "notes":        f"From: {url[:70]}",
                }

    return {
        "phone": None, "email": None,
        "phone_source": "", "source_url": "",
        "notes": "Not found via Google",
    }
