"""Tests for app/processors/deduplicator.py"""
import pytest
from app.models.lead import Lead
from app.processors.deduplicator import deduplicate, _are_same


def _lead(**kwargs) -> Lead:
    defaults = dict(company_name="Test Co", source="TradeIndia",
                    city="Delhi", product_url="https://ti.com/p1")
    defaults.update(kwargs)
    return Lead(**defaults)


class TestAreSame:
    def test_same_valid_phone(self):
        a = _lead(phone_number="+919876543210", phone_status="VALID")
        b = _lead(company_name="Completely Different", phone_number="+919876543210",
                  phone_status="VALID")
        assert _are_same(a, b)

    def test_same_valid_email(self):
        a = _lead(email="contact@gmail.com", email_status="VALID")
        b = _lead(company_name="Other Co", email="contact@gmail.com",
                  email_status="VALID")
        assert _are_same(a, b)

    def test_high_name_similarity_same_city(self):
        a = _lead(company_name="Earth Science Ayurveda", city="Faridabad")
        b = _lead(company_name="Earth Sciences Ayurveda", city="Faridabad")
        assert _are_same(a, b)

    def test_different_city_no_merge(self):
        """Same name but different city — keep separate (could be branches)."""
        a = _lead(company_name="Meena Perfumery", city="Kannauj")
        b = _lead(company_name="Meena Perfumery", city="Delhi")
        assert not _are_same(a, b)

    def test_different_phone_no_merge(self):
        """Different phones AND different names — must not merge."""
        a = _lead(company_name="Alpha Industries", city="Mumbai",
                  phone_number="+919876543210", phone_status="VALID")
        b = _lead(company_name="Beta Chemicals", city="Mumbai",
                  phone_number="+919876543211", phone_status="VALID")
        assert not _are_same(a, b)

    def test_hidden_phone_no_merge_by_phone(self):
        """HIDDEN phones must not be used as merge key."""
        a = _lead(phone_number=None, phone_status="HIDDEN")
        b = _lead(company_name="Unrelated Co", phone_number=None,
                  phone_status="HIDDEN")
        assert not _are_same(a, b)


class TestDeduplicate:
    def test_exact_duplicate_removed(self):
        l1 = _lead(phone_number="+919876543210", phone_status="VALID")
        l2 = _lead(phone_number="+919876543210", phone_status="VALID",
                   product_url="https://ti.com/p2")
        result, merged = deduplicate([l1, l2])
        assert len(result) == 1
        assert merged == 1

    def test_all_product_urls_collected(self):
        l1 = _lead(phone_number="+919876543210", phone_status="VALID",
                   product_url="https://ti.com/p1")
        l2 = _lead(phone_number="+919876543210", phone_status="VALID",
                   product_url="https://ti.com/p2")
        result, _ = deduplicate([l1, l2])
        all_urls = [result[0].product_url] + result[0].extra_product_urls
        assert "https://ti.com/p1" in all_urls
        assert "https://ti.com/p2" in all_urls

    def test_best_phone_status_kept(self):
        """Merged lead should keep VALID over HIDDEN."""
        l1 = _lead(company_name="ABC Co", city="Delhi",
                   phone_number="+919876543210", phone_status="VALID")
        l2 = _lead(company_name="ABC Co", city="Delhi",
                   phone_number=None, phone_status="HIDDEN")
        result, _ = deduplicate([l1, l2])
        assert result[0].phone_status == "VALID"

    def test_distinct_leads_kept_separate(self):
        l1 = _lead(company_name="Alpha Co", city="Mumbai")
        l2 = _lead(company_name="Beta Co", city="Chennai")
        result, merged = deduplicate([l1, l2])
        assert len(result) == 2
        assert merged == 0

    def test_empty_list(self):
        result, merged = deduplicate([])
        assert result == []
        assert merged == 0

    def test_single_lead_unchanged(self):
        l = _lead()
        result, merged = deduplicate([l])
        assert len(result) == 1
        assert merged == 0
