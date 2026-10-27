"""
Google Maps scraper — extract publicly listed business phone numbers.
Uses Playwright to load the Maps page (no API key needed for public data).
Only reads what's visible on the business profile without clicking anything.
"""
import re, time, json
from typing import Optional, Dict

from fetch_contact.utils.phone_extractor import extract_phones, extract_emails

_MAPS_SEARCH = "https://www.google.com/maps/search/{query}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

_JS_EXTRACT_MAPS = """
() => {
    // Extract all visible text from the business panel
    const panel = document.querySelector('[role="main"]') ||
                  document.querySelector('.m6QErb') ||
                  document.body;
    const text = panel ? panel.innerText : document.body.innerText;

    // Look for phone number elements specifically
    const phoneEls = Array.from(document.querySelectorAll('[data-item-id*="phone"], [aria-label*="Phone"]'));
    const phones = phoneEls.map(el => el.innerText || el.getAttribute('aria-label') || '');

    // Look for website link
    const websiteLink = document.querySelector('a[data-item-id*="authority"], a[aria-label*="website"], a[href*="website"]');
    const websiteUrl = websiteLink ? websiteLink.href : null;

    // Business name from panel
    const nameEl = document.querySelector('h1.DUwDvf, h1.fontHeadlineLarge, [data-item-id*="title"]');
    const businessName = nameEl ? nameEl.innerText : '';

    return {
        full_text: text.substring(0, 3000),
        phone_elements: phones,
        website_url: websiteUrl,
        business_name: businessName,
    };
}
"""


def search_google_maps(company: str, city: str,
                       delay: float = 4.0) -> Dict:
    """
    Search Google Maps for a company and extract its publicly listed phone.
    Uses Playwright — no API key needed, no login, no clicking hidden numbers.
    Returns {phone, email, source_url, business_name, website_url, notes}
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return {"phone": None, "notes": "Playwright not available"}

    query = f"{company} {city} India"
    maps_url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"

    result = {
        "phone":         None,
        "email":         None,
        "phone_source":  "GOOGLE_MAPS",
        "source_url":    maps_url,
        "business_name": "",
        "website_url":   None,
        "notes":         "",
    }

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx  = browser.new_context(user_agent=UA, locale="en-IN")
            page = ctx.new_page()
            page.set_default_timeout(30_000)

            time.sleep(delay)
            try:
                page.goto(maps_url, wait_until="domcontentloaded", timeout=30_000)
                # Wait for business panel to load
                try:
                    page.wait_for_selector('[role="main"]', timeout=8_000)
                except PWTimeout:
                    pass
                time.sleep(3)
            except Exception as e:
                result["notes"] = f"Maps load failed: {str(e)[:60]}"
                ctx.close(); browser.close()
                return result

            data = page.evaluate(_JS_EXTRACT_MAPS)
            ctx.close()
            browser.close()

        full_text = data.get("full_text", "")
        phone_els = data.get("phone_elements", [])
        website   = data.get("website_url")
        biz_name  = data.get("business_name", "")

        result["business_name"] = biz_name
        result["website_url"]   = website

        # Try phone elements first (most reliable)
        for p_text in phone_els:
            phones = extract_phones(p_text)
            if phones:
                result["phone"] = phones[0]
                result["notes"] = f"Maps business panel: {biz_name[:40]}"
                return result

        # Try full page text
        phones = extract_phones(full_text)
        if phones:
            result["phone"] = phones[0]
            result["notes"] = f"Maps page text: {biz_name[:40]}"

        # Check for email too
        emails = extract_emails(full_text)
        if emails:
            result["email"] = emails[0]

        if not result["phone"]:
            result["notes"] = f"Phone not visible on Maps for: {biz_name or query}"

        return result

    except Exception as e:
        result["notes"] = f"Maps error: {str(e)[:80]}"
        return result
