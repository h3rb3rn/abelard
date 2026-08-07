# Critical Fixes Test Suite

## Installation

```bash
pip install -r requirements.txt
pytest -v
```

## Purpose

Test suite for validating critical security and functionality fixes:
1. Password hashing (bcrypt migration)
2. JWT secret rotation to ENV var
3. Per-user data isolation (ChromaDB, Neo4j)
4. API authentication on all write endpoints
5. Project user_id FK validation
6. Dockerfile lock file build fix

## Run with pytest

```bash
# All tests
pytest -v tests/critical-fixes/

# Specific suite
pytest -v tests/critical-fixes/test_password_hashing.py
pytest -v tests/critical-fixes/test_jwt_security.py
pytest -v tests/critical-fixes/test_api_auth.py
pytest -v tests/critical-fixes/test_user_isolation.py
pytest -v tests/critical-fixes/test_dockerfile_fix.py
```
