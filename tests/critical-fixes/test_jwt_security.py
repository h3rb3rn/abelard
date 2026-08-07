"""Test suite for JWT security — secrets must not be plaintext, tokens must verify."""

from __future__ import annotations

import base64
import json
import time

import pytest


class TestJWTTokenCreation:
    """Tests that JWT token lifecycle works correctly."""

    def test_create_and_decode_token(self):
        from services.user_service import create_access_token, decode_access_token

        payload = {"sub": "abc123", "exp": int(time.time() + 3600)}
        token = create_access_token(payload)
        decoded = decode_access_token(token)
        assert decoded is not None
        assert decoded["sub"] == "abc123"

    def test_expired_token_returns_none(self):
        from services.user_service import create_access_token, decode_access_token

        payload = {"sub": "test", "exp": int(time.time()) - 1}
        token = create_access_token(payload)
        decoded = decode_access_token(token)
        assert decoded is None

    def test_tampered_token_returns_none(self):
        from services.user_service import create_access_token, decode_access_token

        payload = {"sub": "test", "exp": int(time.time() + 3600)}
        token = create_access_token(payload)

        # Tamper: modify signature byte
        parts = token.split(".")
        tampered_sig = base64.urlsafe_b64encode(b"tampered_signature").rstrip(b"=").decode()
        tampered_token = f"{parts[0]}.{parts[1]}.{tampered_sig}"

        decoded = decode_access_token(tampered_token)
        assert decoded is None


class TestJWTSecretKey:
    """JWT secrets must come from ENV vars, not hardcoded values."""

    def test_secret_is_not_empty(self):
        from services.user_service import SECRET_KEY
        assert len(SECRET_KEY) > 0

    def test_secret_is_not_plaintext_example(self):
        from services.user_service import SECRET_KEY
        bad_examples = [
            "CHANGE-ME-IN-PRODUCTION-32chars",
            "example-secret-key",
            "test-key-do-not-use",
            "",
        ]
        assert SECRET_KEY not in bad_examples

    def test_always_HS256(self):
        from services.user_service import create_access_token
        token = create_access_token({"sub": "test-user"})
        # Verify JWT structure
        parts = token.split(".")
        assert len(parts) == 3
        
        # Decode header
        padding = 4 - len(parts[0]) % 4 if len(parts[0]) % 4 else 0
        header_b64 = parts[0] + "=" * padding
        header = json.loads(base64.urlsafe_b64decode(header_b64))
        assert header["alg"] == "HS256"
        assert header["typ"] == "JWT"
