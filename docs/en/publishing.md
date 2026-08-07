# Publishing & Release Workflow

Two automated scripts in `scripts/` manage pre-release checks and publishing synchronization, preventing credentials, runtime state, or environment-specific data from being committed to the public repository.

## Release Process

```bash
bash scripts/run_security_scan.sh          # 1. Scan codebase for security issues
bash scripts/sync-to-publish.sh --dry-run  # 2. Simulate release sync
bash scripts/sync-to-publish.sh            # 3. Perform release sync (aborts on findings)
```

## `run_security_scan.sh`

Scans the development workspace in four stages:

| Stage | Inspection Target |
|-------|-------------------|
| Secrets | API keys, JWT secrets, private keys, database URIs with passwords, internal IP addresses, private domains |
| Configuration | Hardcoded absolute runtime paths and direct `os.getenv` accesses outside `config.py` |
| SAST | Bandit static Python analysis (filtering severity >= MEDIUM) |
| SCA | Trivy dependency vulnerability scanner (executed via Docker) |

Options: `SEVERITY=MEDIUM,HIGH,CRITICAL`, `SKIP_TRIVY=1` (skips Docker execution). Returns exit code 1 on findings, enabling usage as a CI gate.

## `sync-to-publish.sh`

1. **rsync Sync** — Synchronizes files to the publish repository, excluding `.env`, `docker-compose.override.yml`, runtime logs, caches, agent context, and certificates.
2. **Path Neutralization** — Replaces deployment-specific directory paths with generic placeholders.
3. **Internal Address Check** — Scans for RFC1918 IPs, internal domain names, and deployment hostnames.
4. **Credential Audit** — Re-runs secret detection patterns against all staged files.
5. **Stray File Inspection** — Detects accidentally copied `.env` files, keys, or backup files.

The script exits with code 1 if any issues are detected. Use `--force` to bypass checks (not recommended for production releases) or `--dry-run` to preview changes without modifying files.

## Excluded Artifacts

| File / Directory | Exclusion Rationale |
|------------------|---------------------|
| `.env` | Live production secrets |
| `docker-compose.override.yml` | Environment-specific host and port bindings |
| `searxng/settings.yml` | Instance configuration with secret key |
| `data/`, `chroma-data/` | Runtime logs, vector indices, and uploaded user files |
| `.claude/`, `.codex/`, `.agents/` | AI assistant context files |
| `docs/de/beispiel-auswertung.md` | Real debate session outputs |

Every excluded configuration file maintains a corresponding `.example` template in source control.
