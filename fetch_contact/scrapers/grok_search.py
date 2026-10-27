"""
Grok-powered contact search (fallback if Gemini fails).
Uses xAI Grok API — checks for payment requirements before using.
"""
import os, re
from typing import Dict, Optional
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
XAI_KEY = os.getenv("XAI_API_KEY", "")

from fetch_contact.utils.phone_extractor import extract_phones, extract_emails

GROK_URL = "https://api.x.ai/v1/chat/completions"
_grok_available = None   # cached after first check


def _check_grok_available() -> bool:
    """Check if Grok API is accessible (not paywalled for this key)."""
    global _grok_available
    if _grok_available is not None:
        return _grok_available
    if not XAI_KEY or XAI_KEY.startswith("your_"):
        _grok_available = False
        return False
    # Send a minimal test call
    try:
        r = requests.post(
            GROK_URL,
            headers={"Authorization": f"Bearer {XAI_KEY}",
                     "Content-Type": "application/json"},
            json={"model": "grok-beta",
                  "messages": [{"role": "user", "content": "Say OK"}],
                  "max_tokens": 5},
            timeout=15
        )
        if r.status_code == 200:
            _grok_available = True
            return True
        elif r.status_code in (402, 401, 429):
            _grok_available = False
            return False
        _grok_available = False
        return False
    except Exception:
        _grok_available = False
        return False


def search_contact_grok(company: str, city: str,
                        state: str = "", gst: str = "") -> Dict:
    """
    Use Grok to find phone number for a business.
    Only called if Gemini fails. Returns same dict structure.
    """
    result = {
        "phone": None, "email": None,
        "phone_source": "GROK", "source_url": "", "notes": "",
    }

    if not _check_grok_available():
        result["notes"] = "Grok not available (key invalid or requires payment)"
        return result

    location = f"{city}, {state}".strip(", ") if state else city

    prompt = f"""Find the publicly listed phone number and email for this Indian business:

Company: {company}
Location: {location}
{f'GST: {gst}' if gst else ''}

Search their website, JustDial, IndiaMART, or business directories.
Only return numbers that are directly visible (not hidden behind a login or button).

Reply in this exact format:
PHONE: [number or NOT_FOUND]
EMAIL: [email or NOT_FOUND]
SOURCE: [URL or NOT_FOUND]
NOTES: [context]"""

    try:
        r = requests.post(
            GROK_URL,
            headers={"Authorization": f"Bearer {XAI_KEY}",
                     "Content-Type": "application/json"},
            json={"model": "grok-beta",
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0, "max_tokens": 300},
            timeout=30
        )
        if r.status_code != 200:
            result["notes"] = f"Grok HTTP {r.status_code}"
            return result

        response = (r.json().get("choices", [{}])[0]
                     .get("message", {}).get("content", ""))

        phone_m = re.search(r'PHONE:\s*(.+?)(?:\n|$)', response, re.I)
        email_m = re.search(r'EMAIL:\s*(.+?)(?:\n|$)', response, re.I)
        src_m   = re.search(r'SOURCE:\s*(.+?)(?:\n|$)', response, re.I)
        note_m  = re.search(r'NOTES:\s*(.+?)(?:\n|$)', response, re.I)

        if phone_m:
            raw = phone_m.group(1).strip()
            if "NOT_FOUND" not in raw.upper():
                phones = extract_phones(raw)
                if phones: result["phone"] = phones[0]

        if email_m:
            raw = email_m.group(1).strip()
            if "NOT_FOUND" not in raw.upper() and "@" in raw:
                emails = extract_emails(raw)
                if emails: result["email"] = emails[0]

        if src_m:
            src = src_m.group(1).strip()
            if src.startswith("http"): result["source_url"] = src

        if note_m: result["notes"] = note_m.group(1).strip()

        if not result["phone"]:
            phones = extract_phones(response)
            if phones: result["phone"] = phones[0]

    except Exception as e:
        result["notes"] = f"Grok error: {str(e)[:60]}"

    return result
