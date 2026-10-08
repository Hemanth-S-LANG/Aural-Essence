"""
Phone validator.
  - Uses phonenumbers library with region IN (India).
  - Normalises to E.164 format (+91XXXXXXXXXX).
  - Placeholder strings like "View Number" → HIDDEN.
  - Dummy numbers (all same digit, e.g. 8199999999) → INVALID.
  - Returns (normalised_number_or_None, status).
"""
import re
from typing import Optional, Tuple

import phonenumbers

# Strings that indicate a hidden/masked phone number
_HIDDEN_PATTERNS = re.compile(
    r"view\s+number|call\s+supplier|contact\s+supplier|"
    r"get\s+phone|show\s+number|click\s+to\s+call|"
    r"\*{4,}|x{4,}|\d{4}\*",
    re.IGNORECASE,
)

# Dummy number patterns: 7+ repeated digits, or all-same-digit 10-digit numbers
# e.g. 8199999999, 9876543210 is fine, 9999999999 is dummy
_DUMMY_PATTERN = re.compile(
    r"^(\+91|0)?(\d)\2{8,}$"    # all same digit (e.g. 9999999999, 8888888888)
)

PhoneResult = Tuple[Optional[str], str]   # (normalised, status)


def _is_dummy_number(digits: str) -> bool:
    """Return True if the 10-digit number looks like a placeholder."""
    if len(digits) != 10:
        return False
    # All same digit
    if len(set(digits)) == 1:
        return True
    # Sequential run e.g. 1234567890, 9876543210 — legitimate, not dummy
    # Repeating blocks e.g. 9898989898
    if digits == digits[:5] * 2:
        return True
    return False


def validate_phone(raw: Optional[str]) -> PhoneResult:
    """
    Validate and normalise a phone number string.

    Returns:
        (normalised, status) where status is one of:
          VALID   — parseable Indian number, normalised to +91XXXXXXXXXX
          INVALID — present but not a valid IN number
          HIDDEN  — masked/placeholder string
          MISSING — None or empty
    """
    if not raw:
        return None, "MISSING"

    raw = raw.strip()
    if not raw:
        return None, "MISSING"

    # Check for hidden/placeholder patterns first
    if _HIDDEN_PATTERNS.search(raw):
        return None, "HIDDEN"

    # Try parsing with phonenumbers
    try:
        # Try with explicit IN region first
        parsed = phonenumbers.parse(raw, "IN")
        if phonenumbers.is_valid_number(parsed):
            normalised = phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164
            )
            # Reject dummy numbers (all-same-digit, repeating blocks)
            digits_only = re.sub(r"\D", "", normalised)[-10:]
            if _is_dummy_number(digits_only):
                return None, "INVALID"
            return normalised, "VALID"
        # Try without region (for numbers already in +91 format)
        parsed2 = phonenumbers.parse(raw, None)
        if phonenumbers.is_valid_number(parsed2):
            normalised = phonenumbers.format_number(
                parsed2, phonenumbers.PhoneNumberFormat.E164
            )
            digits_only = re.sub(r"\D", "", normalised)[-10:]
            if _is_dummy_number(digits_only):
                return None, "INVALID"
            return normalised, "VALID"
        return None, "INVALID"
    except phonenumbers.NumberParseException:
        return None, "INVALID"
