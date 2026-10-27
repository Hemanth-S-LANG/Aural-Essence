"""
AI fallback scrapers.
Fallback 1: Gemini (Google AI Studio)
Fallback 2: Grok (xAI)

These are used ONLY when:
  - We found a webpage that likely has a phone but couldn't extract it with regex
  - OR we found a company name + city but no webpage matched

The AI is asked to:
  "Given this text from a business webpage, extract the phone number and email."

API keys are loaded from .env — NEVER hardcoded.
"""
import os, re
from typing import Optional, Dict
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
XAI_KEY    = os.getenv("XAI_API_KEY", "")

from fetch_contact.utils.phone_extractor import extract_phones, extract_emails


def _ask_gemini(prompt: str) -> Optional[str]:
    """Call Gemini API and return text response."""
    if not GEMINI_KEY or GEMINI_KEY == "your_gemini_api_key_here":
        return None
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"gemini-2.0-flash:generateContent?key={GEMINI_KEY}")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 200},
    }
    try:
        r = requests.post(url, json=payload, timeout=20)
        if r.status_code == 200:
            d = r.json()
            return (d.get("candidates", [{}])[0]
                     .get("content", {})
                     .get("parts", [{}])[0]
                     .get("text", ""))
        return None
    except Exception:
        return None


def _ask_grok(prompt: str) -> Optional[str]:
    """Call Grok (xAI) API and return text response."""
    if not XAI_KEY or XAI_KEY == "your_grok_api_key_here":
        return None
    url = "https://api.x.ai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {XAI_KEY}",
               "Content-Type": "application/json"}
    payload = {
        "model":       "grok-beta",
        "messages":    [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens":  200,
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=20)
        if r.status_code == 200:
            return (r.json().get("choices", [{}])[0]
                     .get("message", {})
                     .get("content", ""))
        if r.status_code in (402, 429):
            return None   # payment required or rate limited — don't retry
        return None
    except Exception:
        return None


def _build_extraction_prompt(company: str, city: str,
                              page_text: str, source_url: str) -> str:
    return f"""You are a business contact extraction assistant.

Company: {company}
City: {city}
Source URL: {source_url}

From the following webpage text, extract:
1. The Indian phone number (mobile preferred, format: +91XXXXXXXXXX or 10 digits starting 6-9)
2. The email address

Rules:
- Only extract numbers that are clearly visible in the text
- Do NOT guess or generate numbers
- If no phone is found, say "PHONE: NOT FOUND"
- If no email is found, say "EMAIL: NOT FOUND"
- Return ONLY in this format:
  PHONE: +91XXXXXXXXXX
  EMAIL: example@domain.com

Webpage text (first 1500 chars):
{page_text[:1500]}"""


def extract_with_ai(company: str, city: str,
                    page_text: str, source_url: str) -> Dict:
    """
    Try Gemini first, then Grok, to extract phone/email from page text.
    Returns {phone, email, ai_source, notes}
    """
    result = {"phone": None, "email": None, "ai_source": None, "notes": ""}
    if not page_text.strip():
        return result

    prompt = _build_extraction_prompt(company, city, page_text, source_url)

    # Try Gemini
    response = _ask_gemini(prompt)
    if response:
        result["ai_source"] = "GEMINI"
    else:
        # Try Grok
        response = _ask_grok(prompt)
        if response:
            result["ai_source"] = "GROK"

    if not response:
        result["notes"] = "AI unavailable or no response"
        return result

    # Parse the AI response
    phone_match = re.search(r'PHONE:\s*([+\d\s\-]+)', response)
    email_match = re.search(r'EMAIL:\s*([^\s\n]+@[^\s\n]+)', response)

    if phone_match:
        raw = phone_match.group(1).strip()
        if "NOT FOUND" not in raw.upper():
            phones = extract_phones(raw)
            if phones:
                result["phone"] = phones[0]
                result["notes"] = f"Extracted by {result['ai_source']}"

    if email_match:
        raw = email_match.group(1).strip()
        if "NOT FOUND" not in raw.upper() and "@" in raw:
            result["email"] = raw.lower()

    return result
