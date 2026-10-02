"""
Meesho scraper — graceful no-op.

Reason: Meesho is a D2C marketplace (not a B2B directory).
  - All HTTP requests are blocked by Akamai EdgeSuite WAF (HTTP 403).
  - Even if accessible, seller phone/email/address are not publicly visible.
  - robots.txt disallows /*/search and /*?page=*.

Decision: log BLOCKED / SCOPE_MISMATCH once at startup and return empty lists.
No network requests are made. No retries. No Playwright.
"""
from typing import List, Optional

import yaml
import pathlib

from app.core.logger import get_logger
from app.models.lead import Lead
from app.scrapers.base_scraper import BaseScraper

log = get_logger(__name__)

_YAML_PATH = pathlib.Path(__file__).parent.parent / "config" / "sources.yaml"
with _YAML_PATH.open() as _f:
    _CFG = yaml.safe_load(_f)["meesho"]

_LOGGED = False   # emit the block message only once per run


class MeeshoScraper(BaseScraper):

    is_active: bool = False   # known no-op — skip Phase A in pipeline

    @property
    def source_name(self) -> str:
        return "Meesho"

    def search_listings(self, keyword: str, page: int = 1) -> List[str]:
        global _LOGGED
        if not _LOGGED:
            log.warning(
                _CFG["block_reason"],
                extra={
                    "source": "Meesho",
                    "url": _CFG["base_url"],
                    "stage": "search",
                    "status": "BLOCKED",
                    "reason": "SCOPE_MISMATCH",
                },
            )
            _LOGGED = True
        return []

    def parse_listing(self, html: str, url: str) -> Optional[Lead]:
        # Should never be called since search_listings returns []
        log.warning("MeeshoScraper.parse_listing called unexpectedly",
                    extra={"source": "Meesho", "url": url,
                           "stage": "parse", "status": "BLOCKED"})
        return None
