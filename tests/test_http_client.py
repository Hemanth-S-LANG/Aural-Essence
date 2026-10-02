"""
Tests for app/core/http_client.py — mocked, no live requests.
Covers: block detection (403/429), robots.txt check, HTML caching.
"""
import pytest
from unittest.mock import MagicMock, patch

from app.core.exceptions import BlockedError, RobotsDisallowedError
import app.core.http_client as http_mod


# ── helpers ───────────────────────────────────────────────────────────────────

def _mock_response(status_code: int, text: str = "<html>ok</html>",
                   headers: dict = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.headers = headers or {}
    return resp


# ── block detection ───────────────────────────────────────────────────────────

class TestBlockDetection:
    def test_403_raises_blocked_error(self):
        with patch.object(http_mod, "_fetch_with_retry",
                          side_effect=BlockedError("https://test.com", 403)):
            with patch.object(http_mod, "is_allowed", return_value=True):
                with patch.object(http_mod, "_load_cache", return_value=None):
                    with pytest.raises(BlockedError) as exc_info:
                        http_mod.fetch_html("https://test.com", use_cache=False)
                    assert exc_info.value.status_code == 403

    def test_429_raises_blocked_error(self):
        with patch.object(http_mod, "_fetch_with_retry",
                          side_effect=BlockedError("https://test.com", 429)):
            with patch.object(http_mod, "is_allowed", return_value=True):
                with patch.object(http_mod, "_load_cache", return_value=None):
                    with pytest.raises(BlockedError) as exc_info:
                        http_mod.fetch_html("https://test.com", use_cache=False)
                    assert exc_info.value.status_code == 429


class TestRobotsCheck:
    def test_disallowed_raises_error(self):
        with patch.object(http_mod, "is_allowed", return_value=False):
            with pytest.raises(RobotsDisallowedError):
                http_mod.fetch_html("https://www.tradeindia.com/search/rose-water.html")

    def test_allowed_proceeds(self):
        mock_resp = _mock_response(200, "<html>content</html>")
        with patch.object(http_mod, "is_allowed", return_value=True):
            with patch.object(http_mod, "_load_cache", return_value=None):
                with patch.object(http_mod, "_fetch_with_retry",
                                  return_value=mock_resp):
                    with patch.object(http_mod, "_save_cache"):
                        html = http_mod.fetch_html(
                            "https://www.tradeindia.com/manufacturers/rose-water.html",
                            use_cache=False
                        )
                        assert html == "<html>content</html>"


class TestHtmlCache:
    def test_cache_hit_skips_network(self):
        cached_html = "<html>cached</html>"
        with patch.object(http_mod, "is_allowed", return_value=True):
            with patch.object(http_mod, "_load_cache", return_value=cached_html):
                # _fetch_with_retry should NOT be called
                with patch.object(http_mod, "_fetch_with_retry") as mock_fetch:
                    html = http_mod.fetch_html("https://test.com/", use_cache=True)
                    assert html == cached_html
                    mock_fetch.assert_not_called()

    def test_cache_miss_fetches_and_saves(self):
        mock_resp = _mock_response(200, "<html>fresh</html>")
        with patch.object(http_mod, "is_allowed", return_value=True):
            with patch.object(http_mod, "_load_cache", return_value=None):
                with patch.object(http_mod, "_fetch_with_retry",
                                  return_value=mock_resp):
                    with patch.object(http_mod, "_save_cache") as mock_save:
                        html = http_mod.fetch_html("https://test.com/", use_cache=True)
                        assert html == "<html>fresh</html>"
                        mock_save.assert_called_once()
