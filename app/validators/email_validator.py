"""
Email validator.
  - Uses email-validator library for syntax + DNS-optional check.
  - Rejects obviously junk domains.
  - Returns (normalised_email_or_None, status).
"""
import re
from typing import Optional, Tuple

from email_validator import validate_email, EmailNotValidError

EmailResult = Tuple[Optional[str], str]   # (normalised, status)

# Junk/placeholder domains to explicitly reject
_JUNK_DOMAINS = {
    "example.com", "test.com", "noemail.com", "noreply.com",
    "email.com", "mail.com", "nomail.com", "dummy.com",
}

# Junk local parts
_JUNK_LOCAL = re.compile(r"^(no.?reply|noreply|donotreply|admin|info|test|dummy)$",
                          re.IGNORECASE)


def validate_email_address(raw: Optional[str]) -> EmailResult:
    """
    Validate and normalise an email address.

    Returns:
        (normalised, status) where status is one of:
          VALID   — syntactically valid email
          INVALID — present but invalid or junk
          MISSING — None or empty
    """
    if not raw:
        return None, "MISSING"

    raw = raw.strip()
    if not raw:
        return None, "MISSING"

    try:
        result = validate_email(raw, check_deliverability=False)
        normalised = result.normalized

        # Extra junk checks
        domain = normalised.split("@")[-1].lower()
        local  = normalised.split("@")[0].lower()

        if domain in _JUNK_DOMAINS:
            return None, "INVALID"
        if _JUNK_LOCAL.match(local):
            return None, "INVALID"

        return normalised.lower(), "VALID"
    except EmailNotValidError:
        return None, "INVALID"
