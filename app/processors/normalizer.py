"""
Normaliser — cleans Lead fields in place (returns a new Lead).

Rules:
  - company_name: UPPER, strip common suffixes (Pvt/Private/Ltd/Limited/LLP …)
    for dedup key only; the display name keeps the original cleaned form.
  - address: title-case, collapse whitespace, normalise comma spacing.
  - city/state: title-case, strip.
  - phone: validated + normalised by phone_validator.
  - email: validated + normalised by email_validator.
"""
import re
from typing import Optional

from app.models.lead import Lead
from app.validators.phone_validator import validate_phone
from app.validators.email_validator import validate_email_address

# Suffixes to strip when building a dedup key (not from display name)
_SUFFIX_PATTERN = re.compile(
    r"\b(pvt\.?|private|ltd\.?|limited|llp|inc\.?|corp\.?|"
    r"co\.?|company|enterprises?|industries|works|traders?|"
    r"international|india|exports?|imports?|mfg\.?|mfrs?\.?)\b",
    re.IGNORECASE,
)

_WHITESPACE = re.compile(r"\s{2,}")
_COMMA_SPACE = re.compile(r"\s*,\s*")


def _clean_text(s: Optional[str]) -> Optional[str]:
    """Collapse whitespace, strip."""
    if not s:
        return None
    return _WHITESPACE.sub(" ", s.strip())


def _title(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    return _clean_text(s).title()


def _clean_address(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = _WHITESPACE.sub(" ", s.strip())
    s = _COMMA_SPACE.sub(", ", s)
    return s.title()


def normalise_lead(lead: Lead) -> Lead:
    """
    Return a new Lead with normalised field values and re-run validation.
    Original lead is not mutated.
    """
    # Phone
    norm_phone, phone_status = validate_phone(lead.phone_number)
    # Preserve HIDDEN status set by scraper (number_mask=true); validator
    # returns MISSING for None, but scraper already set HIDDEN.
    if lead.phone_status == "HIDDEN":
        phone_status = "HIDDEN"
        norm_phone = None

    # Email
    # If the email field is None but the upstream status is INVALID (e.g. set by
    # the CSV importer after rejecting a bad address), preserve INVALID rather
    # than overwriting with MISSING.
    if lead.email is None and lead.email_status == "INVALID":
        norm_email, email_status = None, "INVALID"
    else:
        norm_email, email_status = validate_email_address(lead.email)

    return Lead(
        company_name       = _clean_text(lead.company_name) or lead.company_name,
        contact_person     = _clean_text(lead.contact_person),
        designation        = _clean_text(lead.designation),
        phone_number       = norm_phone,
        email              = norm_email,
        address            = _clean_address(lead.address),
        city               = _title(lead.city),
        state              = _title(lead.state),
        gst_no             = (_clean_text(lead.gst_no) or "").upper() or None,
        product_name       = _clean_text(lead.product_name),
        product_url        = lead.product_url,
        source             = lead.source,
        phone_status       = phone_status,
        email_status       = email_status,
        phone_source       = lead.phone_source,   # preserve MANUAL/SCRAPED
        email_source       = lead.email_source,   # preserve MANUAL/SCRAPED
        scraped_at         = lead.scraped_at,
        raw_url            = lead.raw_url,
        extra_product_urls = lead.extra_product_urls,
    )


def dedup_key(lead: Lead) -> str:
    """
    Produce a normalised string key used for deduplication matching.
    Strips legal suffixes, punctuation, and case.
    """
    name = lead.company_name or ""
    name = _SUFFIX_PATTERN.sub("", name)
    name = re.sub(r"[^a-z0-9]", "", name.lower())
    return name
