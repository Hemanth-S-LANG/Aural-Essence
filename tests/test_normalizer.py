"""Tests for app/processors/normalizer.py"""
import pytest
from app.models.lead import Lead
from app.processors.normalizer import dedup_key, normalise_lead


def _lead(**kwargs) -> Lead:
    defaults = dict(company_name="Test Co", source="TradeIndia")
    defaults.update(kwargs)
    return Lead(**defaults)


class TestDedupKey:
    def test_strips_pvt_ltd(self):
        l = _lead(company_name="GIRNAR AYURVEDIC PHARMACY PVT. LTD.")
        key = dedup_key(l)
        assert "pvt" not in key
        assert "ltd" not in key

    def test_strips_private_limited(self):
        l = _lead(company_name="ABC Private Limited")
        key = dedup_key(l)
        assert "private" not in key
        assert "limited" not in key

    def test_case_insensitive(self):
        l1 = _lead(company_name="Earth Science Ayurveda")
        l2 = _lead(company_name="EARTH SCIENCE AYURVEDA")
        assert dedup_key(l1) == dedup_key(l2)

    def test_strips_punctuation(self):
        l1 = _lead(company_name="J. P. PHARMA")
        l2 = _lead(company_name="JP PHARMA")
        assert dedup_key(l1) == dedup_key(l2)

    def test_strips_enterprises(self):
        l = _lead(company_name="Meena Enterprises")
        assert "enterprises" not in dedup_key(l)


class TestNormaliseLead:
    def test_preserves_hidden_phone_status(self):
        """HIDDEN set by scraper must not be overwritten by validator."""
        l = _lead(phone_number=None, phone_status="HIDDEN")
        result = normalise_lead(l)
        assert result.phone_status == "HIDDEN"
        assert result.phone_number is None

    def test_normalises_valid_phone(self):
        l = _lead(phone_number="9876543210", phone_status="MISSING")
        result = normalise_lead(l)
        assert result.phone_status == "VALID"
        assert result.phone_number == "+919876543210"

    def test_normalises_address(self):
        l = _lead(address="  plot no.20,  block-a , kannauj  ")
        result = normalise_lead(l)
        assert result.address == "Plot No.20, Block-A, Kannauj"

    def test_normalises_city_state(self):
        l = _lead(city="FARIDABAD", state="haryana")
        result = normalise_lead(l)
        assert result.city == "Faridabad"
        assert result.state == "Haryana"

    def test_gst_uppercased(self):
        l = _lead(gst_no="06aogpr9376e2zh")
        result = normalise_lead(l)
        assert result.gst_no == "06AOGPR9376E2ZH"

    def test_missing_email_stays_missing(self):
        l = _lead(email=None)
        result = normalise_lead(l)
        assert result.email_status == "MISSING"

    def test_valid_email_normalised(self):
        l = _lead(email="User@Gmail.COM")
        result = normalise_lead(l)
        assert result.email_status == "VALID"
        assert result.email == "user@gmail.com"
