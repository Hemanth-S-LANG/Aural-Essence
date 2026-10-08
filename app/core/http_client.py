"""
HTTP client with:
  - Configurable timeouts
  - Exponential-backoff retries via tenacity (respects Retry-After header)
  - Token-bucket rate limiter (one request per REQUEST_DELAY seconds)
  - Block detection: 403, 429, connection-reset → raises BlockedError
  - Raw HTML caching to data/raw_html/
  - robots.txt check at first use of each domain (cached in memory)
"""
import hashlib
import re
import time
import urllib.robotparser
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from app.config.settings import settings
from app.core.exceptions import BlockedError, RobotsDisallowedError
from app.core.logger import get_logger

log = get_logger(__name__)

USER_AGENT = "AuralEssenceScraper/1.0 (+research-bot; contact: intern@auralEssence.com)"

# Retriable network-level exceptions (not HTTP 4xx)
_RETRIABLE = (
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ConnectionError,
)

# Domains whose robots.txt we've already fetched (domain → RobotFileParser)
_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}

# Last request timestamp per domain for rate limiting
_last_request: dict[str, float] = {}


# ── robots.txt ────────────────────────────────────────────────────────────────

def _get_robots(domain: str) -> urllib.robotparser.RobotFileParser:
    if domain in _robots_cache:
        return _robots_cache[domain]

    robots_url = f"https://{domain}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
        log.info("robots.txt fetched", extra={
            "source": domain, "url": robots_url, "stage": "robots", "status": "OK"
        })
    except Exception as exc:
        log.warning("robots.txt fetch failed — treating all paths as allowed",
                    extra={"source": domain, "url": robots_url,
                           "stage": "robots", "status": "WARN", "error": str(exc)})
        # An empty RobotFileParser.can_fetch() returns False by default,
        # which would wrongly block everything. Explicitly allow all instead.
        rp.allow_all = True
    _robots_cache[domain] = rp
    return rp


def is_allowed(url: str) -> bool:
    parsed = urlparse(url)
    domain = parsed.netloc.lstrip("www.")
    rp = _get_robots(parsed.netloc)
    return rp.can_fetch(USER_AGENT, url)


# ── Rate limiter ──────────────────────────────────────────────────────────────

def _rate_limit(domain: str) -> None:
    """Block until REQUEST_DELAY seconds have passed since the last call for this domain."""
    now = time.monotonic()
    last = _last_request.get(domain, 0.0)
    wait = settings.request_delay - (now - last)
    if wait > 0:
        time.sleep(wait)
    _last_request[domain] = time.monotonic()


# ── HTML cache ────────────────────────────────────────────────────────────────

def _cache_path(url: str) -> Path:
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    return settings.raw_html_dir_abs / f"{url_hash}.html"


def _load_cache(url: str) -> Optional[str]:
    p = _cache_path(url)
    if p.exists():
        return p.read_text(encoding="utf-8")
    return None


def _save_cache(url: str, html: str) -> None:
    _cache_path(url).write_text(html, encoding="utf-8")


# ── Core fetch (with tenacity retry) ─────────────────────────────────────────

_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
        })
    return _session


def _do_fetch(url: str) -> requests.Response:
    """Single HTTP GET — raises on block/error, returns Response on 200."""
    session = _get_session()
    domain = urlparse(url).netloc
    _rate_limit(domain)

    try:
        resp = session.get(url, timeout=settings.request_timeout, allow_redirects=True)
    except _RETRIABLE as exc:
        log.warning("Network error", extra={
            "url": url, "stage": "fetch", "status": "NETWORK_ERROR", "error": str(exc)
        })
        raise  # tenacity will retry

    code = resp.status_code
    log.debug("fetched", extra={"url": url, "stage": "fetch",
                                "status": str(code), "size": len(resp.text)})

    if code == 200:
        return resp

    if code in (403, 503):
        raise BlockedError(url, code)

    if code == 429:
        retry_after = int(resp.headers.get("Retry-After", settings.request_delay * 2))
        log.warning("Rate limited (429) — sleeping", extra={
            "url": url, "stage": "fetch", "status": "429",
            "retry_after": retry_after
        })
        time.sleep(retry_after)
        raise BlockedError(url, 429)

    if code == 404:
        # 404 is a hard failure, not retriable
        raise requests.exceptions.HTTPError(f"404 Not Found: {url}", response=resp)

    raise requests.exceptions.HTTPError(f"HTTP {code}: {url}", response=resp)


# Tenacity wrapper: retry on network errors only, not on BlockedError/404
@retry(
    retry=retry_if_exception_type(_RETRIABLE),
    stop=stop_after_attempt(settings.max_retries),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    before_sleep=before_sleep_log(log, log.level if hasattr(log, 'level') else 20),
    reraise=True,
)
def _fetch_with_retry(url: str) -> requests.Response:
    return _do_fetch(url)


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_html(url: str, use_cache: bool = True) -> str:
    """
    Fetch URL and return HTML text.

    - Checks robots.txt → raises RobotsDisallowedError if blocked.
    - Returns cached HTML if available and use_cache=True.
    - Raises BlockedError on 403/429.
    - Raises requests.exceptions.HTTPError on 404 / other 4xx/5xx.
    - Caches successful responses to data/raw_html/.
    """
    if not is_allowed(url):
        raise RobotsDisallowedError(url)

    if use_cache:
        cached = _load_cache(url)
        if cached:
            log.debug("cache hit", extra={"url": url, "stage": "fetch", "status": "CACHE"})
            return cached

    resp = _fetch_with_retry(url)
    html = resp.text
    _save_cache(url, html)
    return html
