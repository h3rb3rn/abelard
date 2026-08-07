# Security Guidelines

## Security Baseline & Architecture

### 1. Zero Hardcoded Credentials
- No default passwords, fallback API keys, or pre-shared secrets exist in source code.
- Setting `ENVIRONMENT=production` causes application startup to fail if any required secret is missing or set to an example value.

### 2. Multi-Tenancy Isolation
- Every database query strictly filters by `user_id`.
- Requests attempting to access resources owned by other users return `404 Not Found` rather than `403 Forbidden` to prevent resource enumeration attacks.

### 3. Session Guardrails & Isolation
- Real-time cost, token, and turn tracking are isolated per session in Valkey (`debate:{session_id}:...`).
- Global and per-session kill switches provide immediate emergency termination of ongoing debate execution tasks.

### 4. Dependency & Vulnerability Management
- Standard library primitives (`hmac`, `hashlib`) are utilized for password hashing and JWT signatures, minimizing third-party dependency vulnerabilities.
- Static security scanning (`bandit`) and dependency vulnerability checks (`trivy`) are automated via `scripts/run_security_scan.sh`.

## Security Best Practices for Operators

1. **Secrets Management** — Store credentials in `.env` files with strict permissions (`0600`). Exclude `.env` from version control.
2. **Production Secrets** — Always supply non-trivial values generated via cryptographic RNG (`openssl rand -hex 32`).
3. **Container Network Isolation** — Bind database host ports to `127.0.0.1` or restrict external network access via Docker firewall rules.
