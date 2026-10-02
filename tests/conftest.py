"""
Shared pytest fixtures.
All fixtures use saved HTML files — no live HTTP requests.
"""
from pathlib import Path
import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "tradeindia"


@pytest.fixture(scope="session")
def html_listing() -> str:
    return (FIXTURE_DIR / "ti_manufacturers_rosewater_v2.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def html_profile_earth() -> str:
    return (FIXTURE_DIR / "ti_profile_earth_science_ayurve.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def html_profile_falcon() -> str:
    return (FIXTURE_DIR / "ti_profile_falcon.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def html_profile_girnar() -> str:
    return (FIXTURE_DIR / "ti_profile_girnar_ayurvedic_pha.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def html_product_detail() -> str:
    return (FIXTURE_DIR / "ti_product_detail_1.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def html_hm_subdomain() -> str:
    return (FIXTURE_DIR / "ti_product_hm_rose_water.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def meesho_403_html() -> str:
    p = Path(__file__).parent / "fixtures" / "meesho" / "me_403_response.html"
    return p.read_text(encoding="utf-8")
