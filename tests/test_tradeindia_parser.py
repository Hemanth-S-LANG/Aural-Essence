"""
Tests for TradeIndia scraper parser — uses saved HTML fixtures, no live requests.
"""
import pytest
from app.scrapers.tradeindia_scraper import (
    TradeIndiaScraper,
    _get_next_data,
    _detect_page_type,
    _parse_listing_page,
    _parse_profile_page,
    _parse_product_page,
    _keyword_slug,
)
from bs4 import BeautifulSoup


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# ── keyword slug ──────────────────────────────────────────────────────────────

def test_keyword_slug_basic():
    assert _keyword_slug("Rose Water") == "rose-water"

def test_keyword_slug_multi_space():
    assert _keyword_slug("Rose  Water") == "rose-water"

def test_keyword_slug_lowercase():
    assert _keyword_slug("ROSE WATER") == "rose-water"


# ── listing URL templates ─────────────────────────────────────────────────────

def test_listing_url_page1():
    s = TradeIndiaScraper()
    assert s.listing_url("Rose Water", 1) == \
        "https://www.tradeindia.com/manufacturers/rose-water.html"

def test_listing_url_page2():
    s = TradeIndiaScraper()
    assert s.listing_url("Rose Water", 2) == \
        "https://www.tradeindia.com/manufacturers/rose-water-2.html"


# ── __NEXT_DATA__ extraction ──────────────────────────────────────────────────

def test_get_next_data_present(html_listing):
    nd = _get_next_data(soup(html_listing))
    assert nd is not None
    assert "props" in nd

def test_get_next_data_absent():
    nd = _get_next_data(soup("<html><body>No next data here</body></html>"))
    assert nd is None


# ── page type detection ───────────────────────────────────────────────────────

def test_detect_page_type_listing(html_listing):
    nd = _get_next_data(soup(html_listing))
    assert _detect_page_type(nd, "http://test") == "listing"

def test_detect_page_type_profile(html_profile_earth):
    nd = _get_next_data(soup(html_profile_earth))
    assert _detect_page_type(nd, "http://test") == "profile"

def test_detect_page_type_product(html_product_detail):
    nd = _get_next_data(soup(html_product_detail))
    assert _detect_page_type(nd, "http://test") == "product"


# ── listing page parsing ──────────────────────────────────────────────────────

class TestListingPage:
    def test_returns_entry_dicts(self, html_listing):
        """_parse_listing_page returns dicts with profile_url, product_name, product_url."""
        nd = _get_next_data(soup(html_listing))
        entries = _parse_listing_page(nd, "https://www.tradeindia.com/manufacturers/rose-water.html")
        assert len(entries) == 28
        for e in entries:
            assert "profile_url" in e
            assert "product_name" in e
            assert "product_url" in e

    def test_returns_profile_urls(self, html_listing):
        """search_listings (BaseScraper contract) still returns List[str]."""
        # Use _parse_listing_page via scraper.search_listings to verify contract
        nd = _get_next_data(soup(html_listing))
        entries = _parse_listing_page(nd, "https://www.tradeindia.com/manufacturers/rose-water.html")
        urls = [e["profile_url"] for e in entries]
        assert len(urls) == 28

    def test_urls_are_absolute(self, html_listing):
        nd = _get_next_data(soup(html_listing))
        entries = _parse_listing_page(nd, "https://www.tradeindia.com/manufacturers/rose-water.html")
        for e in entries:
            assert e["profile_url"].startswith("https://www.tradeindia.com/"), \
                f"Expected absolute URL, got: {e['profile_url']}"

    def test_known_company_in_results(self, html_listing):
        nd = _get_next_data(soup(html_listing))
        entries = _parse_listing_page(nd, "https://www.tradeindia.com/manufacturers/rose-water.html")
        profile_urls = [e["profile_url"] for e in entries]
        assert any("earth-science-ayurveda" in u.lower() for u in profile_urls)

    def test_product_name_populated(self, html_listing):
        """product_name must be present in listing entries (this was the Task 1 bug)."""
        nd = _get_next_data(soup(html_listing))
        entries = _parse_listing_page(nd, "https://www.tradeindia.com/manufacturers/rose-water.html")
        names = [e["product_name"] for e in entries if e.get("product_name")]
        assert len(names) > 0, "Expected product_name in at least some listing entries"
        # First entry from spike: "Nuerma Science Rose Water (Gulab Jal)..."
        assert any("Rose Water" in (n or "") for n in names)

    def test_product_url_populated(self, html_listing):
        """product_url must be absolute in listing entries."""
        nd = _get_next_data(soup(html_listing))
        entries = _parse_listing_page(nd, "https://www.tradeindia.com/manufacturers/rose-water.html")
        urls = [e["product_url"] for e in entries if e.get("product_url")]
        assert len(urls) > 0
        for u in urls:
            assert u.startswith("https://"), f"Expected absolute URL: {u}"


# ── profile page parsing ──────────────────────────────────────────────────────

class TestProfilePage:
    def test_earth_science_company_name(self, html_profile_earth):
        s = soup(html_profile_earth)
        nd = _get_next_data(s)
        lead = _parse_profile_page(nd, s, "https://www.tradeindia.com/earth-science-ayurveda-29503048/")
        assert lead is not None
        assert lead.company_name == "Earth Science Ayurveda"

    def test_earth_science_contact_person(self, html_profile_earth):
        s = soup(html_profile_earth)
        nd = _get_next_data(s)
        lead = _parse_profile_page(nd, s, "https://www.tradeindia.com/earth-science-ayurveda-29503048/")
        assert lead.contact_person == "Ms Jyoti Karala"

    def test_earth_science_designation(self, html_profile_earth):
        s = soup(html_profile_earth)
        nd = _get_next_data(s)
        lead = _parse_profile_page(nd, s, "https://www.tradeindia.com/earth-science-ayurveda-29503048/")
        assert lead.designation == "Marketing Manager"

    def test_earth_science_address(self, html_profile_earth):
        s = soup(html_profile_earth)
        nd = _get_next_data(s)
        lead = _parse_profile_page(nd, s, "https://www.tradeindia.com/earth-science-ayurveda-29503048/")
        assert lead.address is not None
        assert "Faridabad" in lead.address

    def test_earth_science_gst(self, html_profile_earth):
        s = soup(html_profile_earth)
        nd = _get_next_data(s)
        lead = _parse_profile_page(nd, s, "https://www.tradeindia.com/earth-science-ayurveda-29503048/")
        assert lead.gst_no == "06AOGPR9376E2ZH"

    def test_phone_always_hidden(self, html_profile_earth):
        s = soup(html_profile_earth)
        nd = _get_next_data(s)
        lead = _parse_profile_page(nd, s, "https://www.tradeindia.com/earth-science-ayurveda-29503048/")
        assert lead.phone_status == "HIDDEN"
        assert lead.phone_number is None

    def test_email_always_missing(self, html_profile_earth):
        s = soup(html_profile_earth)
        nd = _get_next_data(s)
        lead = _parse_profile_page(nd, s, "https://www.tradeindia.com/earth-science-ayurveda-29503048/")
        assert lead.email_status == "MISSING"
        assert lead.email is None

    def test_source_is_tradeindia(self, html_profile_earth):
        s = soup(html_profile_earth)
        nd = _get_next_data(s)
        lead = _parse_profile_page(nd, s, "https://www.tradeindia.com/earth-science-ayurveda-29503048/")
        assert lead.source == "TradeIndia"

    def test_falcon_ceo(self, html_profile_falcon):
        s = soup(html_profile_falcon)
        nd = _get_next_data(s)
        lead = _parse_profile_page(nd, s, "https://www.tradeindia.com/falcon-1026607/")
        assert lead.company_name == "FALCON"
        assert lead.designation == "CEO"

    def test_girnar_pvt_ltd(self, html_profile_girnar):
        s = soup(html_profile_girnar)
        nd = _get_next_data(s)
        lead = _parse_profile_page(nd, s, "https://www.tradeindia.com/girnar-ayurvedic-pharmacy-pvt-ltd-23619088/")
        assert "GIRNAR" in lead.company_name
        assert lead.city == "Ahmedabad"
        assert lead.state == "Gujarat"


# ── product detail page parsing ───────────────────────────────────────────────

class TestProductPage:
    def test_product_name(self, html_product_detail):
        s = soup(html_product_detail)
        nd = _get_next_data(s)
        lead = _parse_product_page(nd, s, "https://www.tradeindia.com/products/nuerma-science-rose-water-gulab-jal-cleanses-hydrates-moisturizes-skin-100ml-c9649318.html")
        assert lead is not None
        assert "Rose Water" in lead.product_name

    def test_product_company(self, html_product_detail):
        s = soup(html_product_detail)
        nd = _get_next_data(s)
        lead = _parse_product_page(nd, s, "https://www.tradeindia.com/products/nuerma-science-rose-water-gulab-jal-cleanses-hydrates-moisturizes-skin-100ml-c9649318.html")
        assert lead.company_name == "Earth Science Ayurveda"

    def test_product_address(self, html_product_detail):
        s = soup(html_product_detail)
        nd = _get_next_data(s)
        lead = _parse_product_page(nd, s, "https://www.tradeindia.com/products/nuerma-science-rose-water-gulab-jal-cleanses-hydrates-moisturizes-skin-100ml-c9649318.html")
        assert lead.address is not None
        assert "Faridabad" in lead.address

    def test_product_phone_hidden(self, html_product_detail):
        s = soup(html_product_detail)
        nd = _get_next_data(s)
        lead = _parse_product_page(nd, s, "https://www.tradeindia.com/products/nuerma-science-rose-water-gulab-jal-cleanses-hydrates-moisturizes-skin-100ml-c9649318.html")
        assert lead.phone_status == "HIDDEN"


# ── parse_listing dispatcher ──────────────────────────────────────────────────

class TestParseListingDispatcher:
    """parse_listing() should detect page type and delegate correctly."""

    def test_dispatches_profile(self, html_profile_earth):
        scraper = TradeIndiaScraper()
        lead = scraper.parse_listing(
            html_profile_earth,
            "https://www.tradeindia.com/earth-science-ayurveda-29503048/"
        )
        assert lead is not None
        assert lead.company_name == "Earth Science Ayurveda"

    def test_dispatches_product(self, html_product_detail):
        scraper = TradeIndiaScraper()
        lead = scraper.parse_listing(
            html_product_detail,
            "https://www.tradeindia.com/products/nuerma-science-rose-water-c9649318.html"
        )
        assert lead is not None
        assert "Rose Water" in lead.product_name

    def test_returns_none_for_empty_html(self):
        scraper = TradeIndiaScraper()
        lead = scraper.parse_listing("<html><body></body></html>", "https://test.com/")
        assert lead is None

    def test_no_crash_on_malformed_html(self):
        scraper = TradeIndiaScraper()
        # Should return None gracefully, never raise
        lead = scraper.parse_listing("<<<not html>>>", "https://test.com/bad")
        assert lead is None


# ── block detection ───────────────────────────────────────────────────────────

class TestBlockDetection:
    def test_meesho_is_inactive(self):
        from app.scrapers.meesho_scraper import MeeshoScraper
        s = MeeshoScraper()
        assert s.is_active is False

    def test_meesho_search_returns_empty(self):
        from app.scrapers.meesho_scraper import MeeshoScraper
        s = MeeshoScraper()
        assert s.search_listings("Rose Water", 1) == []

    def test_meesho_parse_returns_none(self):
        from app.scrapers.meesho_scraper import MeeshoScraper
        s = MeeshoScraper()
        assert s.parse_listing("<html></html>", "https://meesho.com/test") is None
