"""
TradeIndia Playwright pagination collector — scroll + Redux state extraction.

Key insight from probe:
  - __NEXT_DATA__ is server-side rendered ONCE and never updates after scroll.
  - Scrolling triggers the React app to call the listing API and update Redux state.
  - The updated seller list lives in window.__REDUX_STATE__ (the Redux store).
  - We read window.__REDUX_STATE__ after each scroll to get the full accumulated list.

Flow:
  1. Load /manufacturers/rose-water.html (establishes session, loads page 1 via SSR).
  2. Read initial sellers from __NEXT_DATA__ (reliable SSR data).
  3. For each subsequent page:
       a. Scroll to bottom → triggers React to call the API and add sellers to Redux state.
       b. Wait for the Redux state's seller count to increase (poll up to 15s).
       c. Read the full seller list from Redux state.
       d. Extract only the new entries (diff vs previous).
       e. Upsert to SQLite immediately (crash-resume).
  4. Stop on has_next=false or consecutive empty scrolls.

Rules:
  - No login, no CAPTCHA, no clicking "View Number"/"Send Inquiry".
  - No proxy rotation. REQUEST_DELAY >= 3s between scrolls.
"""
import json
import random
import re
import time
import urllib.robotparser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Error as PWError

from app.config.settings import settings
from app.core.logger import get_logger
from app.storage.db import get_url_status, upsert_url

log = get_logger(__name__)

_YAML_PATH = Path(__file__).parent.parent / "config" / "sources.yaml"
with _YAML_PATH.open() as _f:
    _CFG = yaml.safe_load(_f)["tradeindia"]

BASE_URL    = _CFG["base_url"]
UA          = "AuralEssenceScraper/1.0 (+research-bot; contact: intern@auralEssence.com)"
_MAX_CONSEC = _CFG.get("max_consecutive_blocks", 5)
_PAUSE_EVERY = 25


def _keyword_slug(keyword: str) -> str:
    return re.sub(r"\s+", "-", keyword.strip().lower())


def _robots_allows(url: str) -> bool:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
    try:
        rp.read()
    except Exception:
        return True   # fetch failed → treat as allowed
    allowed = rp.can_fetch(UA, url)
    log.info(f"robots.txt {'allows' if allowed else 'DISALLOWS'} {url}",
             extra={"source": "TradeIndia", "url": url,
                    "stage": "robots", "status": "OK" if allowed else "BLOCKED"})
    return allowed


# ── JS helpers injected into the browser ─────────────────────────────────────

# Reads the seller list from the Redux store after scroll loads more data.
# TradeIndia's Redux state path:
#   store.getState().manufacturer.manufacturer_page.manufacturer_page_res
#     .mcatData.listing_data.listing_data  (array of seller objects)
_JS_READ_REDUX_SELLERS = """
() => {
    try {
        // Try Redux DevTools extension hook first
        const store = window.__REDUX_STORE__ || window.store;
        if (store && store.getState) {
            const state = store.getState();
            const ld = state?.manufacturer?.manufacturer_page
                            ?.manufacturer_page_res?.mcatData
                            ?.listing_data?.listing_data;
            if (Array.isArray(ld) && ld.length > 0) {
                const pag = state?.manufacturer?.manufacturer_page
                                 ?.manufacturer_page_res?.mcatData
                                 ?.listing_data?.pagination || {};
                return JSON.stringify({ sellers: ld, pagination: pag });
            }
        }
    } catch(e) {}

    // Fallback: re-read __NEXT_DATA__ (only gives page 1, but better than nothing)
    try {
        const el = document.getElementById('__NEXT_DATA__');
        if (el) {
            const nd = JSON.parse(el.textContent);
            const state = nd?.props?.pageProps?.initialState;
            const ld = state?.manufacturer?.manufacturer_page
                            ?.manufacturer_page_res?.mcatData
                            ?.listing_data;
            if (ld && Array.isArray(ld.listing_data)) {
                return JSON.stringify({
                    sellers: ld.listing_data,
                    pagination: ld.pagination || {}
                });
            }
        }
    } catch(e) {}

    return null;
}
"""

# Wait for Redux seller count to exceed `current_count`, polling every 500ms.
_JS_WAIT_FOR_MORE = """
(currentCount) => new Promise((resolve) => {
    const maxWait = 18000;
    const interval = 600;
    let elapsed = 0;
    const poll = () => {
        try {
            const store = window.__REDUX_STORE__ || window.store;
            if (store) {
                const ld = store.getState()?.manufacturer?.manufacturer_page
                               ?.manufacturer_page_res?.mcatData
                               ?.listing_data?.listing_data;
                if (Array.isArray(ld) && ld.length > currentCount) {
                    resolve(ld.length);
                    return;
                }
            }
        } catch(e) {}
        elapsed += interval;
        if (elapsed >= maxWait) { resolve(-1); return; }
        setTimeout(poll, interval);
    };
    setTimeout(poll, interval);
})
"""


def _parse_result(raw: Optional[str]) -> Tuple[List[dict], int, bool, int]:
    """Parse the JS result string → (sellers, total_pages, has_next, current_page)."""
    if not raw:
        return [], 0, False, 1
    try:
        d       = json.loads(raw)
        sellers = d.get("sellers", [])
        pag     = d.get("pagination", {})
        total   = pag.get("total_pages") or pag.get("last_page_no") or 0
        nxt     = pag.get("has_next", False)
        cur     = pag.get("current_page", 1)
        return sellers, total, nxt, cur
    except Exception:
        return [], 0, False, 1


def _entries_from_sellers(sellers: List[dict]) -> List[Dict]:
    entries = []
    for s in sellers:
        pp = s.get("profile_url", "")
        if not pp:
            continue
        profile_url = BASE_URL + pp if pp.startswith("/") else pp
        pd          = s.get("prod_url", "")
        product_url = (BASE_URL + pd if pd.startswith("/") else pd) if pd else None
        entries.append({
            "profile_url":  profile_url,
            "product_name": s.get("product_name"),
            "product_url":  product_url,
            "co_name":      s.get("co_name"),
            "city":         s.get("city"),
            "state":        s.get("state"),
            "number_mask":  s.get("number_mask", True),
        })
    return entries


def collect_listing_pages(
    keyword: str,
    max_pages: Optional[int] = None,
) -> Tuple[List[Dict], int, int]:
    """
    Collect all listing pages by scrolling and reading Redux state.
    Returns (all_entries, total_pages_found, block_count).
    """
    if max_pages is None:
        max_pages = settings.max_pages

    slug        = _keyword_slug(keyword)
    listing_url = f"{BASE_URL}/manufacturers/{slug}.html"
    source      = "TradeIndia"

    if not _robots_allows(listing_url):
        log.warning("robots.txt disallows listing URL",
                    extra={"source": source, "url": listing_url,
                           "stage": "collect", "status": "ROBOTS"})
        return [], 0, 0

    all_entries:   List[Dict] = []
    seen_profiles: set         = set()
    total_pages_found          = 0
    block_count                = 0
    consec_empty               = 0
    pages_collected            = 0
    blocked_statuses:List[int] = []

    def _on_response(response):
        if response.status in (403, 429, 503):
            blocked_statuses.append(response.status)

    log.info(f"Starting scroll collection (max_pages={max_pages})",
             extra={"source": source, "url": listing_url, "stage": "collect"})

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=UA,
            locale="en-IN",
            extra_http_headers={"Accept-Language": "en-IN,en;q=0.9"},
        )
        page = ctx.new_page()
        page.set_default_timeout(55_000)
        page.on("response", _on_response)

        # ── Load page ─────────────────────────────────────────────────────
        try:
            page.goto(listing_url, wait_until="networkidle", timeout=60_000)
        except (PWTimeout, PWError) as exc:
            log.error(f"Failed to load listing page: {exc}",
                      extra={"source": source, "url": listing_url,
                             "stage": "collect", "status": "FAILED"})
            ctx.close(); browser.close()
            return [], 0, 1

        # ── Page 1: read initial sellers ──────────────────────────────────
        raw1 = page.evaluate(_JS_READ_REDUX_SELLERS)
        sellers_p1, total_pages_found, has_next, _ = _parse_result(raw1)

        entries_p1 = _entries_from_sellers(sellers_p1)
        for e in entries_p1:
            if e["profile_url"] not in seen_profiles:
                seen_profiles.add(e["profile_url"])
                all_entries.append(e)
        pages_collected = 1
        _upsert_batch(all_entries, source)

        log.info(
            f"Page 1: {len(entries_p1)} sellers | "
            f"total_pages={total_pages_found} | cumulative={len(seen_profiles)}",
            extra={"source": source, "stage": "collect", "page": 1,
                   "sellers": len(entries_p1), "total_pages": total_pages_found},
        )

        prev_count  = len(sellers_p1)
        target_pages = min(max_pages, total_pages_found) if total_pages_found else max_pages

        # ── Pages 2..N: scroll + wait for Redux state update ──────────────
        for page_num in range(2, target_pages + 1):

            if consec_empty >= _MAX_CONSEC:
                log.warning(
                    f"Stopping: {_MAX_CONSEC} consecutive empty scrolls",
                    extra={"source": source, "stage": "collect",
                           "status": "SOURCE_STOPPED"},
                )
                break

            if len(blocked_statuses) >= _MAX_CONSEC:
                block_count += len(blocked_statuses)
                log.warning(
                    f"Stopping: {block_count} block responses",
                    extra={"source": source, "stage": "collect",
                           "status": "SOURCE_STOPPED"},
                )
                break

            delay = settings.request_delay + random.uniform(0.0, 1.0)
            if page_num % _PAUSE_EVERY == 0:
                delay += 10
                log.info(f"Extra pause at page {page_num} ({delay:.1f}s)",
                         extra={"source": source, "stage": "collect"})
            time.sleep(delay)

            # Scroll to bottom (triggers the site's JS/API call)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

            # Wait for Redux seller count to grow
            new_total = page.evaluate(_JS_WAIT_FOR_MORE, prev_count)

            # Also wait for network to settle
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except PWTimeout:
                pass
            time.sleep(1)

            if new_total == -1 or new_total <= prev_count:
                # Redux didn't update — try reading anyway
                raw2 = page.evaluate(_JS_READ_REDUX_SELLERS)
                sellers_now, tp, has_next2, _ = _parse_result(raw2)
                if tp:
                    total_pages_found = tp
                new_total = len(sellers_now)

                if new_total <= prev_count:
                    consec_empty += 1
                    log.warning(
                        f"Page {page_num}: no new sellers after scroll "
                        f"(DOM={new_total}, prev={prev_count})",
                        extra={"source": source, "stage": "collect",
                               "page": page_num, "status": "EMPTY_SCROLL"},
                    )
                    continue
            else:
                raw2 = page.evaluate(_JS_READ_REDUX_SELLERS)
                sellers_now, tp, has_next2, _ = _parse_result(raw2)
                if tp:
                    total_pages_found = tp

            consec_empty = 0
            # New sellers are the last (new_total - prev_count) entries
            new_batch  = sellers_now[prev_count:]
            prev_count = len(sellers_now)
            new_entries = _entries_from_sellers(new_batch)
            added = 0
            for e in new_entries:
                if e["profile_url"] not in seen_profiles:
                    seen_profiles.add(e["profile_url"])
                    all_entries.append(e)
                    added += 1

            pages_collected += 1
            _upsert_batch(new_entries, source)

            log.info(
                f"Page {page_num}/{target_pages}: "
                f"+{len(new_batch)} sellers, {added} new unique | "
                f"cumulative={len(seen_profiles)}",
                extra={"source": source, "stage": "collect",
                       "page": page_num, "new": len(new_batch),
                       "new_unique": added, "total": len(seen_profiles)},
            )

            if not has_next2:
                log.info(f"has_next=false — all pages collected",
                         extra={"source": source, "stage": "collect"})
                break

        ctx.close()
        browser.close()

    log.info(
        f"Collection done: {pages_collected} pages, "
        f"{len(all_entries)} unique sellers, {block_count} blocks",
        extra={"source": source, "stage": "collect", "status": "DONE",
               "pages": pages_collected, "entries": len(all_entries)},
    )
    return all_entries, total_pages_found, block_count


def _upsert_batch(entries: List[Dict], source: str) -> None:
    for e in entries:
        url = e["profile_url"]
        if get_url_status(url) is None:
            upsert_url(
                url, source, "PENDING",
                metadata={
                    "product_name": e.get("product_name"),
                    "product_url":  e.get("product_url"),
                    "co_name":      e.get("co_name"),
                    "city":         e.get("city"),
                    "state":        e.get("state"),
                },
            )
