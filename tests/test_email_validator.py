"""Tests for app/validators/email_validator.py"""
import pytest
from app.validators.email_validator import validate_email_address


class TestValidEmail:
    def test_gmail(self):
        norm, status = validate_email_address("user@gmail.com")
        assert status == "VALID"
        assert norm == "user@gmail.com"

    def test_uppercase_normalised(self):
        norm, status = validate_email_address("User@Gmail.COM")
        assert status == "VALID"
        assert norm == "user@gmail.com"

    def test_tradeindia_domain(self):
        norm, status = validate_email_address("contact@tradeindia.com")
        assert status == "VALID"


class TestInvalidEmail:
    def test_example_domain(self):
        _, status = validate_email_address("user@example.com")
        assert status == "INVALID"

    def test_no_at_sign(self):
        _, status = validate_email_address("notanemail")
        assert status == "INVALID"

    def test_no_domain(self):
        _, status = validate_email_address("user@")
        assert status == "INVALID"

    def test_junk_local_noreply(self):
        _, status = validate_email_address("noreply@gmail.com")
        assert status == "INVALID"

    def test_junk_local_donotreply(self):
        _, status = validate_email_address("donotreply@gmail.com")
        assert status == "INVALID"


class TestMissingEmail:
    def test_none(self):
        norm, status = validate_email_address(None)
        assert status == "MISSING"
        assert norm is None

    def test_empty(self):
        norm, status = validate_email_address("")
        assert status == "MISSING"
        assert norm is None
