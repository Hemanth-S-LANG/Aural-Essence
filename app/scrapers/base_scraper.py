"""
Abstract base scraper.
Every source scraper must implement search_listings and parse_listing.
The pipeline calls only these two methods — it never cares which site produced a lead.
"""
from abc import ABC, abstractmethod
from typing import List, Optional

from app.models.lead import Lead


class BaseScraper(ABC):

    # Set to False for scrapers that are known no-ops (e.g. Meesho)
    # so the pipeline skips Phase A entirely and avoids false selector_broken counts.
    is_active: bool = True

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Human-readable source label e.g. 'TradeIndia'."""

    @abstractmethod
    def search_listings(self, keyword: str, page: int = 1) -> List[str]:
        """
        Return a list of absolute URLs for individual listing/profile pages
        found on the given search/category page.

        Must not raise — return [] on any error and log it.
        """

    @abstractmethod
    def parse_listing(self, html: str, url: str) -> Optional[Lead]:
        """
        Parse one listing/profile page and return a (possibly partial) Lead.

        Must not raise — return None on any parse failure and log it.
        company_name is required; return None if it cannot be found.
        """
