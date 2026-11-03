"""
Extract and validate Indian phone numbers from raw text.
Uses the phonenumbers library for proper IN validation.
Also filters out dummy/placeholder numbers.
"""
import re
from typing import Optional, List
import phonenumbers

# Broad pattern — catch everything, validate after
# Handles: +91 8047852380, +918047852380, 08047852380, 8047852380
# Also handles spaces/dashes inside: +91 80478 52380, 9876 543 210
_BROAD_PATTERN = re.compile(
    r'(?:\+91|0091|91)?'          # optional country code
    r'[\s\-\.]?'                   # optional separator
    r'[6-9]'                       # must start with 6-9 (Indian mobile)
    r'[\d\s\-\.]{8,12}'           # 8-12 more digits (with possible separators)
)

# Also catch 0-prefixed landline numbers like 08045801773
_LANDLINE_PATTERN = re.compile(
    r'\b0[1-9]\d{8,10}\b'
)

# Dummy numbers to reject
_DUMMY_PATTERNS = re.compile(
    r'^([6-9])\1{9}$|'            # all same digit: 9999999999
    r'^(\d{5})\2$',               # repeating block: 9898989898
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
    Returns list of E.164-formatted numbers (+91XXXXXXXXXX), deduplicated.
    """
    if not text:
        return []
    if _HIDDEN.search(text):
        return []

    found = []

    # Collect all raw matches
    raw_matches = []
    for m in _BROAD_PATTERN.finditer(text):
        raw_matches.append(m.group())
    for m in _LANDLINE_PATTERN.finditer(text):
        raw_matches.append(m.group())

    for raw in raw_matches:
        # Strip everything except digits
        digits = re.sub(r'\D', '', raw)

        # Normalise country code
        if digits.startswith('0091'):
            digits = digits[4:]
        elif digits.startswith('91') and len(digits) == 12:
            digits = digits[2:]
        elif digits.startswith('0') and len(digits) == 11:
            digits = digits[1:]

        if len(digits) != 10:
            continue

        # Must start with 6-9
        if digits[0] not in '6789':
            continue

        # Reject dummies
        if _DUMMY_PATTERNS.match(digits):
            continue

        # Validate with phonenumbers library
        try:
            parsed = phonenumbers.parse(f'+91{digits}', None)
            if not phonenumbers.is_valid_number(parsed):
                continue
            e164 = phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164)
            if e164 not in found:
                found.append(e164)
        except phonenumbers.NumberParseException:
            continue

    # Mobile numbers first
    if prefer_mobile:
        found.sort(key=lambda n: 0 if n[3] in '6789' else 1)

    return found


def best_phone(text: str) -> Optional[str]:
    phones = extract_phones(text)
    return phones[0] if phones else None


def extract_emails(text: str) -> List[str]:
    if not text:
        return []
    pattern = r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
    junk_local  = re.compile(r'^(noreply|no-reply|donotreply|admin|info|test|dummy)$', re.I)
    junk_domain = {'example.com', 'test.com', 'noemail.com'}
    results = []
    for m in re.finditer(pattern, text):
        email = m.group().lower()
        local, domain = email.split('@', 1)
        if domain in junk_domain or junk_local.match(local):
            continue
        if email not in results:
            results.append(email)
    return results
