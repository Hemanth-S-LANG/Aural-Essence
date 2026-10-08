"""
Deduplicator.

Merging strategy (conservative — only merge with strong evidence):
  - Two leads are the SAME if any of:
      1. Same normalised phone (VALID only)
      2. Same normalised email (VALID only)
      3. Very high name similarity (rapidfuzz ratio ≥ 92) AND same city
  - On merge: keep best value per field, collect all product URLs.
  - Different cities with similar names → keep separate (could be branches).

Output: deduplicated list of Leads, count of merges.
"""
import re
from typing import List, Tuple

from rapidfuzz import fuzz

from app.models.lead import Lead
from app.processors.normalizer import dedup_key
from app.core.logger import get_logger

log = get_logger(__name__)

NAME_SIMILARITY_THRESHOLD = 92   # rapidfuzz ratio (0–100)


def _best(a, b):
    """Return whichever value is truthy (prefer a)."""
    return a if a else b


def _merge(base: Lead, other: Lead) -> Lead:
    """Merge `other` into `base`, keeping best values, collecting all URLs."""
    urls = list({
        u for u in ([base.product_url] + base.extra_product_urls +
                    [other.product_url] + other.extra_product_urls)
        if u
    })
    primary_url = urls[0] if urls else base.product_url
    extra_urls  = urls[1:] if len(urls) > 1 else []

    # Keep the phone/email with the best status (VALID > HIDDEN > MISSING > INVALID)
    _rank = {"VALID": 0, "HIDDEN": 1, "MISSING": 2, "INVALID": 3}
    if _rank.get(base.phone_status, 3) <= _rank.get(other.phone_status, 3):
        phone, p_status = base.phone_number, base.phone_status
    else:
        phone, p_status = other.phone_number, other.phone_status

    if _rank.get(base.email_status, 3) <= _rank.get(other.email_status, 3):
        email, e_status = base.email, base.email_status
    else:
        email, e_status = other.email, other.email_status

    return Lead(
        company_name   = _best(base.company_name, other.company_name),
        contact_person = _best(base.contact_person, other.contact_person),
        designation    = _best(base.designation, other.designation),
        phone_number   = phone,
        email          = email,
        address        = _best(base.address, other.address),
        city           = _best(base.city, other.city),
        state          = _best(base.state, other.state),
        gst_no         = _best(base.gst_no, other.gst_no),
        product_name   = _best(base.product_name, other.product_name),
        product_url    = primary_url,
        extra_product_urls = extra_urls,
        products_count = max(base.products_count, other.products_count),
        source         = base.source if base.source == other.source
                         else f"{base.source}+{other.source}",
        phone_status   = p_status,
        email_status   = e_status,
        scraped_at     = base.scraped_at,
        raw_url        = base.raw_url,
    )


def _are_same(a: Lead, b: Lead) -> bool:
    """Return True if leads should be merged."""
    # 1. Same valid phone
    if (a.phone_status == "VALID" and b.phone_status == "VALID"
            and a.phone_number and a.phone_number == b.phone_number):
        return True

    # 2. Same valid email
    if (a.email_status == "VALID" and b.email_status == "VALID"
            and a.email and a.email.lower() == b.email.lower()):
        return True

    # 3. High name similarity + same city (case-insensitive)
    city_a = (a.city or "").lower().strip()
    city_b = (b.city or "").lower().strip()
    if city_a and city_b and city_a == city_b:
        ratio = fuzz.ratio(dedup_key(a), dedup_key(b))
        if ratio >= NAME_SIMILARITY_THRESHOLD:
            return True

    return False


def deduplicate(leads: List[Lead]) -> Tuple[List[Lead], int]:
    """
    Deduplicate a list of Leads.

    Returns:
        (deduped_leads, merge_count)
    """
    merged_count = 0
    result: List[Lead] = []

    for lead in leads:
        matched = False
        for i, existing in enumerate(result):
            if _are_same(existing, lead):
                result[i] = _merge(existing, lead)
                merged_count += 1
                matched = True
                log.debug(f"Merged '{lead.company_name}' into '{existing.company_name}'",
                          extra={"stage": "dedup", "status": "MERGED"})
                break
        if not matched:
            result.append(lead)

    log.info(f"Deduplication: {len(leads)} → {len(result)} leads ({merged_count} merged)",
             extra={"stage": "dedup", "status": "OK",
                    "input": len(leads), "output": len(result),
                    "merged": merged_count})
    return result, merged_count
