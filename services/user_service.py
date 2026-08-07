"""User management: register, login, JWT auth."""

from __future__ import annotations

import uuid
import hashlib
import hmac as hmac_module
from datetime import datetime, timezone, timedelta
from typing import Optional, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from models.db import User


class UserService:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def register(self, email: str, password: str, name: str = "") -> User:
        """Registriert einen neuen User - Tenant-Basis."""
        existing = await self.get_by_email(email)
        if existing:
            raise ValueError("email already registered")

        user = User(
            id=uuid.uuid4(),
            email=email.strip().lower(),
            password_hash=self._hash_password(password),
            name=name.strip()[:128],
            is_active=True,
            created_at=datetime.now(tz=timezone.utc),
        )
        self.db.add(user)
        await self.db.flush()
        return user

    async def login(self, email: str, password: str) -> Optional[User]:
        """Login - gibt User oder None zurück."""
        user = await self.get_by_email(email)
        if not user:
            return None
        if not self._verify_password(password, user.password_hash):
            return None
        return user

    async def get_by_id(self, uid: uuid.UUID) -> Optional[User]:
        res = await self.db.execute(select(User).where(User.id == uid))
        return res.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[User]:
        res = await self.db.execute(select(User).where(User.email == email.strip().lower()))
        return res.scalar_one_or_none()

    @staticmethod
    def _hash_password(password: str) -> str:
        salt = uuid.uuid4().hex
        hashed = hashlib.sha256((salt + password).encode()).hexdigest()
        return f"{salt}:{hashed}"

    @staticmethod
    def _verify_password(plain: str, stored_hash: str) -> bool:
        try:
            salt, expected = stored_hash.split(":")
        except ValueError:
            return False
        actual = hashlib.sha256((salt + plain).encode()).hexdigest()
        return hmac_module.compare_digest(actual, expected)


# =================================================================== JWT Tokens (HMAC-SHA256 — no crypto libs needed) =======

import base64
import hashlib as _hashlib
import hmac as _hmac
import json as _json

SECRET_KEY = __import__("os").environ.get("JWT_SECRET", "fixed-secret-key-for-jwt-signing")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24


def create_access_token(data: dict) -> str:
    import base64 as _b64

    header = {"alg": "HS256", "typ": "JWT"}
    exp_val = data.get("exp", int((datetime.now(tz=timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)).timestamp()))
    payload = {**data, "exp": exp_val}

    h_b64 = _b64.urlsafe_b64encode(__import__("json").dumps(header).encode()).rstrip(b"=").decode()
    p_b64 = _b64.urlsafe_b64encode(__import__("json").dumps(payload).encode()).rstrip(b"=").decode()

    sig_input = f"{h_b64}.{p_b64}"
    signature = _b64.urlsafe_b64encode(
        __import__("hmac").new(SECRET_KEY.encode(), sig_input.encode(), __import__("hashlib").sha256).digest()
    ).rstrip(b"=").decode()

    return f"{sig_input}.{signature}"


def decode_access_token(token: str) -> Optional[dict]:
    parts = token.split(".")
    if len(parts) != 3:
        return None

    header_b64, payload_b64, sig = parts
    import base64 as _b64
    import json as _json

    try:
        header = _json.loads(_b64.urlsafe_b64decode(header_b64 + "==" * (4 - len(header_b64) % 4)))
        payload = _json.loads(_b64.urlsafe_b64decode(payload_b64 + "==" * (4 - len(payload_b64) % 4)))
    except Exception:
        return None

    if header.get("alg") != "HS256":
        return None

    sig_input = f"{header_b64}.{payload_b64}"
    expected_sig = _b64.urlsafe_b64encode(
        __import__("hmac").new(SECRET_KEY.encode(), sig_input.encode(), __import__("hashlib").sha256).digest()
    ).rstrip(b"=").decode()

    if not __import__("hmac").compare_digest(expected_sig, sig):
        return None

    exp = datetime.fromtimestamp(payload.get("exp", 0), tz=timezone.utc)
    now = datetime.now(tz=timezone.utc)
    if now > exp:
        return None

    return payload


def _base64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

def _base64url_decode(s: str) -> bytes:
    pad = "=" * ((4 - len(s) % 4) % 4) if len(s) % 4 else ""
    return base64.urlsafe_b64decode(s + pad)

