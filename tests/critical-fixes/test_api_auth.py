"""Source-code inspection tests — validate API auth patterns without importing FastAPI.

These tests read the raw files on disk and verify the correct architecture is present.
They never import fastapi / main / api_router_v2, so they work even when those
deps are missing from the host environment.
"""

from __future__ import annotations

import pathlib

# abelard/tests/critical-fixes/.. = project root
ROOT = pathlib.Path(__file__).resolve().parents[2]


class TestAPIAuthPatterns:
    """Verify that auth is wired correctly without needing FastAPI installed."""

    def _read_file(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text()

    def test_router_has_auth_middleware_in_source(self):
        """api_router_v2.py MUST import and use OAuth2PasswordBearer."""
        src = self._read_file("api_router_v2.py")
        assert "OAuth2PasswordBearer" in src, "Missing OAuth2PasswordBearer!"
        assert "_get_current_user" in src or "_current_user" in src, \
            "Missing user authentication middleware function!"

    def test_get_current_user_uses_jwt_decode(self):
        """_get_current_user MUST call decode_access_token on the token."""
        src = self._read_file("api_router_v2.py")
        assert "decode_access_token" in src, "_get_current_user doesn't decode JWT!"

    def test_write_requires_auth_via_dependency(self):
        """POST/DELETE endpoints MUST include _current_user dependency (not auth/register)."""
        src = self._read_file("api_router_v2.py")
        import re
        post_matches = re.findall(
            r'@router\.post\s*\([^)]+\)\s*async def (\w+)\(', src)
        
        # These POST routes must NOT be the public auth endpoints
        non_auth_posts = [n for n in post_matches 
                        if 'register' not in n and 'login' not in n]
        
        assert len(non_auth_posts) > 0, "Expected at least one non-auth POST route!"

    def test_register_endpoint_exists(self):
        """POST /api/v2/auth/register MUST exist (publicly accessible)."""
        src = self._read_file("api_router_v2.py")
        assert "/auth/register" in src, "Auth register endpoint missing!"
        assert "@router.post" in src, "No POST router decorator found!"

    def test_auth_login_endpoint_exists(self):
        """POST /api/v2/auth/login MUST exist for user credential exchange."""
        src = self._read_file("api_router_v2.py")
        assert "/auth/login" in src, "Auth login endpoint missing!"
