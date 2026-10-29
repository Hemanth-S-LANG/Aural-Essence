"""
Google Search scraper — browser automation only. No API keys.

Uses the already-open Chrome (CDP port 9222) which has real Google
cookies — no bot detection normally.

When CAPTCHA appears:
  - Prints a loud message in terminal
  - Brings Chrome to front
  - Waits up to 10 min for user to solve it
  - Detects solve by checking if real search results loaded
    (checks URL AND DOM — not just URL, which was the bug before)
  - Resumes automatically once solved
"""
import re, time, random, subprocess
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


# ── Browser management ────────────────────────────────────────────────────────

def _get_page():
    """Get a page — uses existing Chrome via CDP first, then launches fresh."""
    global _BROWSER, _PAGE, _PW

    try:
        if _PAGE:
            _PAGE.title()
            return _PAGE
    except Exception:
        _PAGE = None

    from playwright.sync_api import sync_playwright

    # Try CDP — existing Chrome has real Google cookies
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

    # Fallback: launch with saved profile (visible, not headless)
    _PW = sync_playwright().start()
    _BROWSER = _PW.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False, channel="chrome",
        args=["--no-sandbox",
              "--disable-blink-features=AutomationControlled",
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


# ── CAPTCHA detection & waiting ───────────────────────────────────────────────

def _has_real_results(page) -> bool:
    """Returns True if the page has real Google search results loaded."""
    try:
        return page.evaluate(r"""
            () => {
                return !!(
                    document.querySelector('#search') ||
                    document.querySelector('#rso')    ||
                    document.querySelector('.g')      ||
                    document.querySelector('h3')      ||
                    document.querySelector('[data-ved]')
                );
            }
        """)
    except Exception:
        return False


def _is_captcha(page) -> bool:
    """Returns True if CAPTCHA/sorry page is blocking results."""
    try:
        url = page.url or ""
        on_sorry = "/sorry/" in url or "google.com/sorry" in url
        if not on_sorry:
            return False
        return not _has_real_results(page)
    except Exception:
        return False


def _is_throttled(page) -> bool:
    """
    Returns True if Google is silently throttling —
    page loaded but has NO results AND no CAPTCHA.
    This is the silent block that causes rows to show 'not found'
    even though we're not on /sorry/ page.
    """
    try:
        url = page.url or ""
        # If on /sorry/ — that's CAPTCHA, handled separately
        if "/sorry/" in url:
            return False
        # If on a real Google search URL but no results in DOM — throttled
        is_google_search = "google.com/search" in url
        if not is_google_search:
            return False
        has_results = _has_real_results(page)
        # Also check body text length — throttled pages are nearly empty
        body_len = page.evaluate("() => document.body.innerText.length") or 0
        return not has_results and body_len < 500
    except Exception:
        return False


def _bring_chrome_front():
    """Try to bring Chrome window to front so user sees the CAPTCHA."""
    try:
        subprocess.Popen(
            ['powershell', '-Command',
             '$wshell = New-Object -ComObject wscript.shell; '
             '$wshell.AppActivate("Google Chrome")'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception:
        pass


def _wait_captcha(page, timeout_min: int = 10) -> bool:
    """
    Wait for user to solve the CAPTCHA.
    Checks every 10s whether real results have appeared (DOM check),
    not just URL check — this was the bug causing it to miss the solve.
    """
    _bring_chrome_front()

    print(f"\n{'='*65}")
    print(f"  *** CAPTCHA — SOLVE IN CHROME WINDOW ***")
    print(f"{'='*65}")
    print(f"  1. Switch to the Chrome window")
    print(f"  2. Tick 'I am not a robot'")
    print(f"  3. Script auto-resumes once Google shows results")
    print(f"  Timeout: {timeout_min} minutes")
    print(f"{'='*65}\n")

    for i in range(timeout_min * 6):   # every 10 seconds
        time.sleep(10)
        try:
            # Primary check: real search results in DOM
            if _has_real_results(page):
                print(f"  ✓ Results detected — CAPTCHA solved! Resuming...\n")
                time.sleep(2)
                return True

            # Secondary check: URL moved completely away from /sorry/
            url = page.url or ""
            if "/sorry/" not in url and "google.com/sorry" not in url:
                print(f"  ✓ URL clear ({url[:50]}) — resuming...\n")
                time.sleep(2)
                return True

        except Exception:
            pass  # page may be navigating — keep waiting

        elapsed = (i + 1) * 10
        if elapsed % 30 == 0:
            print(f"  Waiting {elapsed}s... (solve CAPTCHA in Chrome)")

    print("  CAPTCHA timeout — skipping this seller.\n")
    return False


# ── Google search ─────────────────────────────────────────────────────────────

def _search_google(query: str) -> Dict:
    """
    Navigate to Google, search the query, extract phones/emails.
    Handles both CAPTCHA (/sorry/ page) and silent throttling (empty results).
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
        time.sleep(3)

        # ── CAPTCHA check (/sorry/ page) ─────────────────────────────────
        if _is_captcha(page):
            solved = _wait_captcha(page, timeout_min=10)
            if not solved:
                return result
            if "/sorry/" in (page.url or ""):
                page.goto(
                    f"https://www.google.com/search?q={encoded}&hl=en&gl=in",
                    wait_until="domcontentloaded",
                    timeout=25_000,
                )
                time.sleep(3)

        # ── Silent throttle check (empty page, no CAPTCHA) ───────────────
        if _is_throttled(page):
            wait_secs = random.randint(30, 60)
            print(f"\n  [!] Google throttling detected — waiting {wait_secs}s...")
            _bring_chrome_front()
            time.sleep(wait_secs)
            # Retry the search once after waiting
            page.goto(
                f"https://www.google.com/search?q={encoded}&hl=en&gl=in",
                wait_until="domcontentloaded",
                timeout=25_000,
            )
            time.sleep(3)
            # If still throttled/CAPTCHA after backoff — give up on this query
            if _is_throttled(page) or _is_captcha(page):
                if _is_captcha(page):
                    _wait_captcha(page, timeout_min=10)
                else:
                    print(f"  [!] Still throttled after backoff — skipping\n")
                    return result

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

        # Extract from AI Overview + Knowledge Panel + snippets
        rich = page.evaluate(r"""
            () => {
                var parts = [];
                [
                    '[data-attrid*="overview"]', '[class*="wDYxhc"]',
                    '[class*="ILfuVd"]',  '[class*="LGOjhe"]',
                    '[class*="ai-overview"]', '[class*="SGlybb"]',
                    '[jsname="yEVEwb"]',  '.xpdopen',
                    '[data-attrid="wa:/description"]',
                    '.ifM9O', '.c2xzTb', '.V3FYCf',
                    '[data-attrid*="phone"]', '[data-attrid*="telephone"]',
                    '.VwiC3b', '.s3v9rd', '.IsZvec', '.aCOpRe',
                    '[data-sncf="1"]'
                ].forEach(function(sel) {
                    document.querySelectorAll(sel).forEach(function(el) {
                        var t = el.innerText.trim();
                        if (t.length > 10) parts.push(t);
                    });
                });
                return parts.join('\n\n');
            }
        """) or ""

        combined = rich + "\n\n" + full_text
        result["phones"] = list(dict.fromkeys(extract_phones(combined)))
        result["emails"] = list(dict.fromkeys(extract_emails(combined)))

        urls = page.evaluate(r"""
            () => {
                var seen = new Set(), links = [];
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
    """Visit a URL with requests and scrape phone/email."""
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
        soup   = BeautifulSoup(r.text, "lxml")
        text   = soup.get_text(" ", strip=True)
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


# ── Main entry point ──────────────────────────────────────────────────────────

def search_seller_contact(company: str, city: str, state: str = "",
                          address: str = "", delay: float = 2.0) -> Dict:
    """Search Google for a seller's contact. Query: 'Company City contact details'."""
    location = city.strip() or state.strip()
    queries  = []
    if location:
        queries.append(f"{company} {location} contact details")
    if state.strip() and state.strip() != location:
        queries.append(f"{company} {state.strip()} contact details")
    queries.append(f"{company} contact phone India")

    for qi, query in enumerate(queries[:2]):
        if qi > 0:
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
