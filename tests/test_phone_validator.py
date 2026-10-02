"""Tests for app/validators/phone_validator.py"""
import pytest
from app.validators.phone_validator import validate_phone


class TestValidPhone:
    def test_e164_format(self):
        num, status = validate_phone("+919876543210")
        assert status == "VALID"
        assert num == "+919876543210"

    def test_10_digit_without_prefix(self):
        num, status = validate_phone("9876543210")
        assert status == "VALID"
        assert num == "+919876543210"

    def test_with_spaces(self):
        num, status = validate_phone("98765 43210")
        assert status == "VALID"
        assert num == "+919876543210"

    def test_with_dash(self):
        num, status = validate_phone("98765-43210")
        assert status == "VALID"
        assert num == "+919876543210"

    def test_landline_with_std(self):
        # 011-XXXXXXXX Delhi landline
        num, status = validate_phone("011-46710423")
        assert status == "VALID"


class TestHiddenPhone:
    def test_view_number(self):
        _, status = validate_phone("View Number")
        assert status == "HIDDEN"

    def test_call_supplier(self):
        _, status = validate_phone("Call Supplier")
        assert status == "HIDDEN"

    def test_contact_supplier(self):
        _, status = validate_phone("Contact Supplier")
        assert status == "HIDDEN"

    def test_masked_stars(self):
        _, status = validate_phone("98765*****")
        assert status == "HIDDEN"

    def test_get_phone(self):
        _, status = validate_phone("Get Phone")
        assert status == "HIDDEN"


class TestMissingPhone:
    def test_none(self):
        num, status = validate_phone(None)
        assert status == "MISSING"
        assert num is None

    def test_empty_string(self):
        num, status = validate_phone("")
        assert status == "MISSING"
        assert num is None

    def test_whitespace_only(self):
        num, status = validate_phone("   ")
        assert status == "MISSING"


class TestInvalidPhone:
    def test_too_short(self):
        _, status = validate_phone("12345")
        assert status == "INVALID"

    def test_letters(self):
        _, status = validate_phone("abcdefghij")
        assert status == "INVALID"

    def test_all_zeros(self):
        _, status = validate_phone("0000000000")
        assert status == "INVALID"
