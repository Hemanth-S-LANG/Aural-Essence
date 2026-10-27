"""
Extract and validate Indian phone numbers from raw text.
Uses the phonenumbers library for proper IN validation.
Also filters out dummy/placeholder numbers.
"""
import re
from typing import Optional, List
import phonenumbers

# Patterns to find phone numbers in text
_PHONE_PATTERNS = [
    r'\+91[\s\-\.]?[6-9]\d{9}',           # +91 XXXXXXXXXX
    r'0?[6-9]\d{9}',                        # 10-digit mobile
    r'\b[6-9]\d{4}[\s\-\.]?\d{5}\b',      # XXXXX XXXXX
    r'\b0\d{2,4}[\s\-\.]?\d{6,8}\b',      # landline
    r'\+91[\s\-\.]?\d{5}[\s\-\.]?\d{5}',  # +91 XXXXX XXXXX
]

# Dummy numbers to reject
_DUMMY_PATTERNS = re.compile(
    r'^(\+91|0)?([6-9])\2{9}$|'       # all same digit: 9999999999
    r'^(\+91|0)?(\d{5})\4$',           # repeating block: 9898989898
    re.IGNORECASE
)

# Context words that indicate a phone number nearby
_PHONE_CONTEXT = re.compile(
    r'(phone|mobile|mob|cell|tel|contact|call|ph\.?|m\.?)\s*[:\-]?\s*',
    re.IGNORECASE
)

_HIDDEN = re.compile(
    r'view\s+number|call\s+supplier|contact\s+supplier|'
    r'get\s+phone|show\s+number|\*{4,}|x{4,}',
    re.IGNORECASE
)


def extract_phones(text: str, prefer_mobile: bool = True) -> List[str]:
    """
    Extract all valid Indian phone numbers from text.
    Returns list of E.164-formatted numbers, deduplicated.
    """
    if not text:
        return []

    # Reject hidden/placeholder strings
    if _HIDDEN.search(text):
        return []

    found = []
    combined_pattern = '|'.join(_PHONE_PATTERNS)

    for match in re.finditer(combined_pattern, text):
        raw = match.group().strip()
        digits = re.sub(r'\D', '', raw)

        # Must be 10 or 12 digits (with 91 prefix)
        if len(digits) == 12 and digits.startswith('91'):
            digits = digits[2:]
        if len(digits) != 10:
            continue

        # Skip dummy numbers
        if _DUMMY_PATTERNS.match(digits):
            continue

        # Validate with phonenumbers
        try:
            parsed = phonenumbers.parse(f'+91{digits}', None)
            if not phonenumbers.is_valid_number(parsed):
                continue
            e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
            if e164 not in found:
                found.append(e164)
        except phonenumbers.NumberParseException:
            continue

    # Sort: mobile numbers (6-9 prefix) before landlines
    if prefer_mobile:
        found.sort(key=lambda n: 0 if n[3] in '6789' else 1)

    return found


def best_phone(text: str) -> Optional[str]:
    """Return the single best phone number from text, or None."""
    phones = extract_phones(text)
    return phones[0] if phones else None


def extract_emails(text: str) -> List[str]:
    """Extract email addresses from text."""
    if not text:
        return []
    pattern = r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
    junk_local = re.compile(r'^(noreply|no-reply|donotreply|admin|info|test|dummy)$', re.I)
    junk_domain = {'example.com','test.com','noemail.com'}
    results = []
    for m in re.finditer(pattern, text):
        email = m.group().lower()
        local, domain = email.split('@', 1)
        if domain in junk_domain or junk_local.match(local):
            continue
        if email not in results:
            results.append(email)
    return results
