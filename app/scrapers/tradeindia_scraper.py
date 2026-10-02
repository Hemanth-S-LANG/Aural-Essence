"""
TradeIndia scraper.

Page types handled:
  1. Manufacturer listing  — /manufacturers/{keyword-slug}.html
     • 28 sellers per page in __NEXT_DATA__.mcatData.listing_data.listing_data
     • yields profile_url for each seller → goes into url_status as PENDING

  2. Seller profile         — /{company-slug}-{id}/
     • Full address, owner_name, designation, gst_no in __NEXT_DATA__
     • Phone always hidden (number_mask=true) → phone_status=HIDDEN

  3. Product detail         — /products/{slug}.html
     • product_name + same company_details block as profile page

Parse priority: __NEXT_DATA__ JSON → JSON-LD → CSS selectors from sources.yaml.

Pagination note: robots.txt disallows *?page=* so we use path-based pagination:
  page 1 → /manufacturers/rose-water.html
  page 2 → /manufacturers/rose-water-2.html  (verified pattern)
"""
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import yaml
from bs4 import BeautifulSoup

from app.config.settings import settings
from app.core.exceptions import ParseError, SelectorBrokenError
from app.core.logger import get_logger
from app.models.lead import Lead
from app.scrapers.base_scraper import BaseScraper

log = get_logger(__name__)

# ── Load sources.yaml once at import time ─────────────────────────────────────
_SOURCES_FILE = settings.__class__.__module__  # locate package
import pathlib
_YAML_PATH = pathlib.Path(__file__).parent.parent / "config" / "sources.yaml"
with _YAML_PATH.open() as _f:
    _CFG = yaml.safe_load(_f)["tradeindia"]

BASE_URL          = _CFG["base_url"]
LISTING_TEMPLATE  = _CFG["listing_url_template"]    # legacy / backward-compat
PAGE2_TEMPLATE    = _CFG["listing_page2_template"]  # legacy / backward-compat
LISTING_CATEGORIES = _CFG.get("listing_categories", [
    # fallback if yaml ever missing the new key
    {"name": "manufacturers",
     "page1_template": LISTING_TEMPLATE,
     "page2_template": PAGE2_TEMPLATE},
])
LISTING_MAP      = _CFG["listing_field_map"]
PROFILE_MAP      = _CFG["profile_field_map"]
PDP_MAP          = _CFG["pdp_company_field_map"]
CSS              = _CFG["css_selectors"]
MAX_CONSEC_BLOCKS = _CFG["max_consecutive_blocks"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _keyword_slug(keyword: str) -> str:
    """'Rose Water' → 'rose-water'"""
    return re.sub(r"\s+", "-", keyword.strip().lower())


def _abs(path: str) -> str:
    """Turn a relative TradeIndia path into an absolute URL."""
    if not path:
        return ""
    if path.startswith("http"):
        return path
    return BASE_URL + path


def _get_next_data(soup: BeautifulSoup) -> Optional[Dict[str, Any]]:
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except json.JSONDecodeError as exc:
        log.warning("__NEXT_DATA__ JSON parse error", extra={
            "stage": "parse", "status": "WARN", "error": str(exc)
        })
        return None


def _dig(d: Any, *keys: str) -> Any:
    """Safely walk a nested dict/list by key sequence. Returns None on miss."""
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _extract_by_map(block: Dict[str, Any], field_map: Dict[str, str]) -> Dict[str, Any]:
    """Apply a field_map dict to a JSON block → {lead_field: value}."""
    return {
        lead_field: block.get(json_key)
        for lead_field, json_key in field_map.items()
        if block.get(json_key) is not None
    }


def _phone_email_status(data: Dict[str, Any]) -> Dict[str, str]:
    """
    Derive phone_status and email_status from parsed data dict.
    number_mask=True → HIDDEN.  value present → we validate later; default MISSING.
    """
    number_mask = data.get("number_mask", False)
    phone       = data.get("phone_number") or data.get("phone")
    email       = data.get("email")

    if number_mask:
        phone_status = "HIDDEN"
    elif phone:
        phone_status = "MISSING"  # will be re-validated in Phase 4
    else:
        phone_status = "MISSING"

    email_status = "MISSING" if not email else "MISSING"  # validated in Phase 4
    return {"phone_status": phone_status, "email_status": email_status}


# ── CSS fallback helpers ───────────────────────────────────────────────────────

def _css_company(soup: BeautifulSoup) -> Optional[str]:
    tag = soup.select_one(CSS["company_name"])
    if tag:
        # Strip city suffix e.g. "H.M.ENTERPRISES - Kannauj"
        text = tag.get_text(strip=True)
        return re.split(r"\s+[-–|]\s+", text)[0].strip() or None
    return None


def _css_address(soup: BeautifulSoup) -> Optional[str]:
    tag = soup.select_one(CSS["address"])
    return tag.get_text(" ", strip=True) if tag else None


def _json_ld_company(soup: BeautifulSoup) -> Optional[str]:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(tag.string or "")
            if d.get("@type") == "LocalBusiness":
                return d.get("name")
        except Exception:
            pass
    return None


def _json_ld_address(soup: BeautifulSoup) -> Optional[str]:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(tag.string or "")
            if d.get("@type") == "LocalBusiness":
                addr = d.get("address", {})
                if isinstance(addr, dict):
                    parts = [
                        addr.get("streetAddress", ""),
                        addr.get("addressLocality", ""),
                        addr.get("addressRegion", ""),
                        addr.get("postalCode", ""),
                        addr.get("addressCountry", ""),
                    ]
                    return ", ".join(p for p in parts if p) or None
                if isinstance(addr, str):
                    return addr
        except Exception:
            pass
    return None


# ── Page-type detection ────────────────────────────────────────────────────────

def _detect_page_type(nd: Dict[str, Any], url: str) -> str:
    """
    Returns 'listing' | 'profile' | 'product' | 'unknown'
    based on which __NEXT_DATA__ branch has data.
    """
    state = _dig(nd, "props", "pageProps", "initialState")
    if state is None:
        return "unknown"

    # listing
    listing = _dig(state, "manufacturer", "manufacturer_page",
                   "manufacturer_page_res", "mcatData", "listing_data", "listing_data")
    if isinstance(listing, list) and listing:
        return "listing"

    # profile
    cd = _dig(state, "sellerProfile", "seller_profile",
              "seller_profile_res", "company_details_data")
    if isinstance(cd, dict) and cd.get("co_name"):
        return "profile"

    # product detail
    pd = _dig(state, "product", "PDP_page", "PDP_page_res", "product_details")
    if isinstance(pd, dict) and pd.get("product_name"):
        return "product"

    return "unknown"


# ── Parse functions per page type ─────────────────────────────────────────────

def _parse_listing_page(nd: Dict[str, Any], url: str) -> List[Dict[str, Any]]:
    """
    Extract seller entries from a manufacturer listing page.
    Returns list of dicts, each with:
      profile_url  – absolute URL to the seller profile page (the PENDING URL)
      product_name – product name from the listing (carried as metadata)
      product_url  – absolute product detail URL (carried as metadata)
    """
    state = _dig(nd, "props", "pageProps", "initialState")
    sellers = _dig(state, "manufacturer", "manufacturer_page",
                   "manufacturer_page_res", "mcatData", "listing_data", "listing_data")
    if not sellers:
        return []

    entries = []
    for s in sellers:
        profile_path = s.get(LISTING_MAP.get("profile_url", "profile_url"))
        if not profile_path:
            continue
        prod_path = s.get(LISTING_MAP.get("product_url", "prod_url"), "")
        entries.append({
            "profile_url":  _abs(profile_path),
            "product_name": s.get(LISTING_MAP.get("product_name", "product_name")),
            "product_url":  _abs(prod_path) if prod_path else None,
        })

    log.info(f"Listing page: found {len(entries)} sellers",
             extra={"source": "TradeIndia", "url": url,
                    "stage": "listing_parse", "status": "OK"})
    return entries


def _parse_profile_page(nd: Dict[str, Any], soup: BeautifulSoup,
                        url: str) -> Optional[Lead]:
    """Parse a seller profile page into a Lead."""
    state = _dig(nd, "props", "pageProps", "initialState")
    cd = _dig(state, "sellerProfile", "seller_profile",
              "seller_profile_res", "company_details_data") or {}

    # Pull fields via map
    data = _extract_by_map(cd, PROFILE_MAP)

    # Ensure company_name
    co = data.get("company_name") or _json_ld_company(soup) or _css_company(soup)
    if not co:
        log.warning("No company_name on profile page", extra={
            "source": "TradeIndia", "url": url, "stage": "parse", "status": "SKIP"
        })
        return None

    # Address fallback
    addr = data.get("address") or _json_ld_address(soup) or _css_address(soup)

    statuses = _phone_email_status(data)

    # Try to get a product URL from "more products" on this profile
    product_url = None
    more_prods = _dig(state, "sellerMoreProducts", "seller_more_products",
                      "seller_more_products_res", "data")
    if isinstance(more_prods, list) and more_prods:
        first_prod = more_prods[0]
        if isinstance(first_prod, dict):
            p_path = first_prod.get("prod_url") or first_prod.get("product_url")
            product_url = _abs(p_path) if p_path else None

    return Lead(
        company_name   = co,
        contact_person = data.get("contact_person"),
        designation    = data.get("designation"),
        phone_number   = data.get("phone"),
        email          = data.get("email"),
        address        = addr,
        city           = data.get("city"),
        state          = data.get("state"),
        gst_no         = data.get("gst_no"),
        product_name   = None,   # filled from product URL if we fetch it
        product_url    = product_url or url,
        source         = "TradeIndia",
        phone_status   = statuses["phone_status"],
        email_status   = statuses["email_status"],
        raw_url        = url,
    )


def _parse_product_page(nd: Dict[str, Any], soup: BeautifulSoup,
                        url: str) -> Optional[Lead]:
    """Parse a product detail page into a Lead."""
    state = _dig(nd, "props", "pageProps", "initialState")
    pd    = _dig(state, "product", "PDP_page", "PDP_page_res", "product_details") or {}
    cd    = _dig(state, "product", "PDP_page", "PDP_page_res", "company_details") or {}

    co = cd.get(PDP_MAP.get("company_name", "co_name")) or _json_ld_company(soup) or _css_company(soup)
    if not co:
        return None

    data = _extract_by_map(cd, PDP_MAP)
    addr = data.get("address") or _json_ld_address(soup)
    statuses = _phone_email_status(data)

    prod_path = pd.get("prod_url", "")
    product_url = _abs(prod_path) if prod_path else url

    return Lead(
        company_name   = co,
        contact_person = data.get("contact_person"),
        designation    = data.get("designation"),
        phone_number   = None,
        email          = None,
        address        = addr,
        city           = data.get("city"),
        state          = data.get("state"),
        gst_no         = data.get("gst_no"),
        product_name   = pd.get("product_name"),
        product_url    = product_url,
        source         = "TradeIndia",
        phone_status   = statuses["phone_status"],
        email_status   = statuses["email_status"],
        raw_url        = url,
    )


# ── Scraper class ─────────────────────────────────────────────────────────────

class TradeIndiaScraper(BaseScraper):

    @property
    def source_name(self) -> str:
        return "TradeIndia"

    def listing_url(self, keyword: str, page: int, category: str = "manufacturers") -> str:
        """Return the listing page URL for a given keyword, page, and category."""
        slug = _keyword_slug(keyword)
        # Find the category config
        cat_cfg = next(
            (c for c in LISTING_CATEGORIES if c["name"] == category),
            LISTING_CATEGORIES[0],
        )
        if page == 1:
            return cat_cfg["page1_template"].format(keyword_slug=slug)
        return cat_cfg["page2_template"].format(keyword_slug=slug, page=page)

    def _fetch_listing_entries(
        self, keyword: str, page: int, category: str = "manufacturers"
    ) -> List[Dict[str, Any]]:
        """
        Fetch one listing page for a given category and return entry dicts
        (profile_url, product_name, product_url).
        Returns [] on any error.
        """
        from app.core.http_client import fetch_html
        from app.core.exceptions import BlockedError, RobotsDisallowedError

        url = self.listing_url(keyword, page, category)
        log.info(f"Fetching listing page {page} [{category}]", extra={
            "source": "TradeIndia", "url": url, "stage": "search",
            "status": "START", "category": category, "page": page,
        })

        try:
            html = fetch_html(url)
        except RobotsDisallowedError:
            log.warning("robots.txt disallows listing URL", extra={
                "source": "TradeIndia", "url": url, "stage": "search", "status": "ROBOTS"
            })
            return []
        except BlockedError as exc:
            log.warning("Listing page blocked", extra={
                "source": "TradeIndia", "url": url, "stage": "search",
                "status": "BLOCKED", "http_code": exc.status_code
            })
            return []
        except Exception as exc:
            log.error("Unexpected error fetching listing", extra={
                "source": "TradeIndia", "url": url, "stage": "search",
                "status": "FAILED", "error": str(exc)
            })
            return []

        soup = BeautifulSoup(html, "lxml")
        nd   = _get_next_data(soup)
        if nd is None:
            log.warning("No __NEXT_DATA__ on listing page — possible JS shell",
                        extra={"source": "TradeIndia", "url": url,
                               "stage": "search", "status": "WARN"})
            return []

        entries = _parse_listing_page(nd, url)
        if not entries:
            log.warning(
                f"SELECTOR_BROKEN TradeIndia — listing returned 0 sellers "
                f"[category={category}, page={page}]",
                extra={"source": "TradeIndia", "url": url,
                       "stage": "search", "status": "SELECTOR_BROKEN",
                       "category": category, "page": page},
            )
        return entries

    def search_listings(self, keyword: str, page: int = 1) -> List[str]:
        """BaseScraper contract: return profile URLs only (manufacturers category only)."""
        entries = self._fetch_listing_entries(keyword, page, category="manufacturers")
        return [e["profile_url"] for e in entries]

    def search_listings_meta(self, keyword: str, page: int = 1) -> List[Dict[str, Any]]:
        """
        Extended version used by the pipeline: iterates ALL configured categories
        for the given page number, deduplicates by profile_url, returns full dicts.
        """
        seen: set = set()
        all_entries: List[Dict[str, Any]] = []
        for cat in LISTING_CATEGORIES:
            entries = self._fetch_listing_entries(keyword, page, cat["name"])
            for e in entries:
                if e["profile_url"] not in seen:
                    seen.add(e["profile_url"])
                    all_entries.append(e)
        return all_entries

    def parse_listing(self, html: str, url: str) -> Optional[Lead]:
        """
        Parse a seller profile page or product detail page.
        Detects page type automatically from __NEXT_DATA__.
        Returns None on any failure.
        """
        soup = BeautifulSoup(html, "lxml")
        nd   = _get_next_data(soup)

        if nd is None:
            # Attempt JSON-LD + CSS fallback
            co   = _json_ld_company(soup) or _css_company(soup)
            addr = _json_ld_address(soup) or _css_address(soup)
            if not co:
                log.warning("Cannot find company name (no __NEXT_DATA__, no JSON-LD, no CSS)",
                            extra={"source": "TradeIndia", "url": url,
                                   "stage": "parse", "status": "SKIP"})
                return None
            log.warning("Fell back to JSON-LD/CSS (no __NEXT_DATA__)",
                        extra={"source": "TradeIndia", "url": url,
                               "stage": "parse", "status": "FALLBACK"})
            return Lead(
                company_name = co,
                address      = addr,
                product_url  = url,
                source       = "TradeIndia",
                phone_status = "MISSING",
                email_status = "MISSING",
                raw_url      = url,
            )

        page_type = _detect_page_type(nd, url)
        log.debug(f"Page type: {page_type}", extra={
            "source": "TradeIndia", "url": url, "stage": "parse"
        })

        try:
            if page_type == "profile":
                return _parse_profile_page(nd, soup, url)
            elif page_type == "product":
                return _parse_product_page(nd, soup, url)
            elif page_type == "listing":
                # Should not normally land here, but handle gracefully:
                # extract first seller's profile and return a partial lead
                sellers = _dig(nd, "props", "pageProps", "initialState",
                               "manufacturer", "manufacturer_page",
                               "manufacturer_page_res", "mcatData",
                               "listing_data", "listing_data") or []
                if sellers:
                    s = sellers[0]
                    co = s.get(LISTING_MAP["company_name"])
                    if co:
                        return Lead(
                            company_name = co,
                            city         = s.get(LISTING_MAP.get("city", "city")),
                            state        = s.get(LISTING_MAP.get("state", "state")),
                            product_name = s.get(LISTING_MAP.get("product_name", "product_name")),
                            product_url  = _abs(s.get(LISTING_MAP.get("product_url", "prod_url"), "")),
                            source       = "TradeIndia",
                            phone_status = "HIDDEN" if s.get("number_mask") else "MISSING",
                            email_status = "MISSING",
                            raw_url      = url,
                        )
                return None
            else:
                log.warning("Unknown page type", extra={
                    "source": "TradeIndia", "url": url, "stage": "parse",
                    "status": "UNKNOWN_PAGE"
                })
                return None

        except Exception as exc:
            log.error("Parse error", extra={
                "source": "TradeIndia", "url": url, "stage": "parse",
                "status": "FAILED", "error": str(exc)
            })
            return None
