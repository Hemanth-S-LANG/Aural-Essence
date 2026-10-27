"""
Gemini-powered contact search — Google AI Mode style.

Uses the EXACT same approach as Google AI Mode on the phone:
  Query: "super honey lucknow contact details"
  → Gemini searches the web and returns a natural language answer
    with phone, email, address, owner name directly in the text.

Two query strategies tried in order:
  1. Simple natural language: "{company} {city} contact details"
  2. Fallback structured:     "{company} {city} phone number email address"

Extracts phones/emails from the free-text response using regex —
no rigid format required from the model.
"""
import os, re, time
from typing import Dict, Optional
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")

from fetch_contact.utils.phone_extractor import extract_phones, extract_emails

# Use the latest available models (confirmed from API model list)
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-3.8-flash:generateContent?key={key}"
)
GEMINI_LITE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-flash-lite-latest:generateContent?key={key}"
)


def _call_gemini(query: str, use_grounding: bool = True,
                 lite: bool = False) -> Optional[str]:
    """
    Call Gemini with Google Search grounding.
    query — the natural language search query (exactly like Google AI Mode).
    Returns the response text, or None on failure.
    """
    if not GEMINI_KEY or GEMINI_KEY.startswith("your_"):
        return None

    url = (GEMINI_LITE_URL if lite else GEMINI_URL).format(key=GEMINI_KEY)

    payload = {
        "contents": [{"parts": [{"text": query}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 1024},
    }
    if use_grounding:
        payload["tools"] = [{"google_search": {}}]

    try:
        r = requests.post(url, json=payload, timeout=30)

        if r.status_code == 200:
            data = r.json()
            parts = (data.get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", []))
            return " ".join(p.get("text", "") for p in parts).strip()

        if r.status_code == 429:
            # Rate limited — wait and retry with lite model
            time.sleep(8)
            if not lite:
                return _call_gemini(query, use_grounding, lite=True)

        if r.status_code == 400:
            # Grounding not available on this key tier — try without
            if use_grounding:
                return _call_gemini(query, use_grounding=False, lite=lite)

        if r.status_code in (500, 503):
            time.sleep(5)
            if not lite:
                return _call_gemini(query, use_grounding, lite=True)

        return None
    except Exception:
        return None


def search_contact_gemini(company: str, city: str,
                          state: str = "", gst: str = "") -> Dict:
    """
    Find phone/email for an Indian business using Gemini + Google Search.

    Uses simple natural language queries exactly like Google AI Mode:
      "super honey lucknow contact details"
      "laxmipati sales jaipur contact details"

    Returns: {phone, email, phone_source, source_url, notes}
    """
    result = {
        "phone":        None,
        "email":        None,
        "phone_source": "GEMINI",
        "source_url":   "",
        "notes":        "",
    }

    if not GEMINI_KEY or GEMINI_KEY.startswith("your_"):
        result["notes"] = "GEMINI_KEY not set"
        return result

    location = city.strip() if city.strip() else state.strip()
    company  = company.strip()

    # ── Build queries in order of specificity ────────────────────────────────
    queries = []

    # Query 1: exactly like Google AI Mode — "company city contact details"
    if location:
        queries.append(f"{company} {location} contact details")

    # Query 2: with state if different from city
    if state.strip() and state.strip().lower() != location.lower():
        queries.append(f"{company} {state.strip()} contact details")

    # Query 3: explicit phone request
    if location:
        queries.append(f"{company} {location} phone number mobile number")
    else:
        queries.append(f"{company} India contact phone number")

    # Query 4: with GST for uniqueness
    if gst:
        queries.append(f"{company} GST {gst} contact phone")

    # ── Try each query ────────────────────────────────────────────────────────
    for i, query in enumerate(queries):
        if i > 0:
            time.sleep(3)

        response = _call_gemini(query, use_grounding=True)
        if not response:
            continue

        # Extract all phones from the natural language response
        phones = extract_phones(response)
        emails = extract_emails(response)

        if phones:
            # Pick best phone — prefer 10-digit mobile, reject known bad numbers
            best_phone = None
            for p in phones:
                digits = re.sub(r"\D", "", p)
                if len(digits) == 10 and digits[0] in "6789":
                    best_phone = f"+91{digits}"
                    break
                elif len(digits) == 12 and digits.startswith("91"):
                    best_phone = f"+{digits}"
                    break
            if best_phone:
                # Try to find a URL in the response
                url_match = re.search(r'https?://[^\s\)\"\']+', response)
                source_url = url_match.group(0).rstrip(".,") if url_match else ""

                result["phone"]      = best_phone
                result["email"]      = emails[0] if emails else None
                result["source_url"] = source_url
                result["notes"]      = f"Query: {query[:80]}"
                return result

        # Even if no phone, capture email for partial credit
        if emails and not result["email"]:
            result["email"] = emails[0]
            result["notes"] = f"Email only from: {query[:60]}"

    # No phone found — return with email if any
    if not result["notes"]:
        result["notes"] = "Not found via Gemini AI search"
    return result
