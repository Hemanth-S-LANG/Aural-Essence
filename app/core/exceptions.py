"""
Project-specific exceptions.
Keeping them in one file makes it easy to catch them at pipeline boundaries.
"""


class ScraperError(Exception):
    """Base for all scraper errors."""


class BlockedError(ScraperError):
    """HTTP 403/429/connection-reset — page actively blocked."""
    def __init__(self, url: str, status_code: int = 0, message: str = ""):
        self.url = url
        self.status_code = status_code
        super().__init__(message or f"Blocked fetching {url} (HTTP {status_code})")


class RobotsDisallowedError(ScraperError):
    """URL is disallowed by robots.txt."""
    def __init__(self, url: str):
        self.url = url
        super().__init__(f"robots.txt disallows: {url}")


class ParseError(ScraperError):
    """HTML/JSON parsing failed for a page."""
    def __init__(self, url: str, reason: str = ""):
        self.url = url
        super().__init__(f"Parse error on {url}: {reason}")


class SelectorBrokenError(ScraperError):
    """Parser returned empty results on a page that should have data."""
    def __init__(self, source: str, url: str):
        self.source = source
        self.url = url
        super().__init__(f"SELECTOR_BROKEN {source} — empty result on {url}")


class StorageError(ScraperError):
    """SQLite read/write failure."""


class ExportError(ScraperError):
    """Excel export failure."""
