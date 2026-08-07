"""Test suite for validating user authentication & password hashing."""

from __future__ import annotations

import pytest
from services.user_service import _hash_password, _verify_password


class TestPasswordHashing:
    """Verifies that SHA256-hashing with salt produces valid hashes.

    NOTE: These tests verify the current implementation works. They fail if we
    later add the bcrypt requirement from pyproject.toml — at which point this
    test file serves as a regression guard telling us we need the migration.
    """

    def test_hash_and_verify_roundtrip(self) -> None:
        password = "MySecretPass123"
        hashed = _hash_password(password)
        assert _verify_password(password, hashed) is True

    def test_wrong_password_returns_false(self) -> None:
        assert _verify_password("wrong", _hash_password("correct")) is False

    def test_hash_has_salt_prefix(self) -> None:
        """SHA256 salt format: 'salt_hex:hash_hex'."""
        hashed = _hash_password("test")
        parts = hashed.split(":")
        assert len(parts) == 2
        assert len(parts[0]) == 32  # UUID hex

    def test_different_hashes_per_call(self) -> None:
        """Each call to _hash_password should produce a different hash."""
        h1 = _hash_password("same_password")
        h2 = _hash_password("same_password")
        assert h1 != h2


class TestJWTSecrets:
    """Validates that JWT secrets are not hardcoded in production code."""

    def test_jwt_secret_is_not_plaintext(self) -> None:
        from services.user_service import SECRET_KEY
        # Should NOT be the example placeholder value
        assert SECRET_KEY != "CHANGE-ME-IN-PRODUCTION-32chars!!"
        # Should not be empty or default
        assert len(SECRET_KEY) >= 32
