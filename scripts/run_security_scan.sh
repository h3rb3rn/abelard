#!/usr/bin/env bash
# run_security_scan.sh — Abelard: Sicherheitspruefung vor der Veroeffentlichung
#
# Fuehrt vier Pruefungen aus:
#   1. Secrets   — eigener Scan auf Zugangsdaten und interne Adressen im Arbeitsverzeichnis
#   2. Config    — hardcodierte Werte, die in die Konfiguration gehoeren
#   3. SAST      — Bandit (statische Python-Analyse)
#   4. SCA       — Trivy (Abhaengigkeits-Schwachstellen, via Docker)
#
# Exit-Code: 0 wenn alles besteht, 1 bei Funden.
#
# Usage:
#   bash scripts/run_security_scan.sh
#   SEVERITY=MEDIUM,HIGH,CRITICAL bash scripts/run_security_scan.sh
#   SKIP_TRIVY=1 bash scripts/run_security_scan.sh    # ohne Docker

set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'
info()   { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()   { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()   { echo -e "${RED}[FAIL]${NC}  $*" >&2; }
header() { echo -e "\n${BOLD}$*${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SEVERITY="${SEVERITY:-HIGH,CRITICAL}"
OVERALL_EXIT=0

COMMON_EXCL=(--exclude-dir='.git' --exclude-dir='node_modules' --exclude-dir='__pycache__'
             --exclude-dir='.pytest_cache' --exclude-dir='site' --exclude-dir='data'
             --exclude='*.example' --exclude='*.sample')

# ── 1. Secrets & interne Adressen ────────────────────────────────────────────
header "=== 1/4 Secrets & interne Adressen ==="
info "Ziel: ${PROJECT_ROOT} (ohne .env — die wird nie veroeffentlicht)"

# Zugangsdaten werden ueberall gesucht — auch in Tests, dort hat kein echter
# Schluessel etwas verloren.
SECRET_PATTERNS=(
  "OPENAI-KEY|\\bsk-[A-Za-z0-9_-]{20,}"
  "ANTHROPIC-KEY|\\bsk-ant-[A-Za-z0-9_-]{20,}"
  "AWS-AKIA|(AKIA|ASIA)[A-Z0-9]{16}"
  "GH-PAT|gh[opsur]_[A-Za-z0-9]{36,251}"
  "JWT|\\beyJ[A-Za-z0-9_-]{10,}\\.eyJ[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}\\b"
  "PRIVATE-KEY|-----BEGIN[A-Z ]*PRIVATE KEY-----"
  "BEKANNTE-DEFAULTS|[:=][[:space:]]*[\x27\"]?(neo4jpassword|debateengine123|fixed-secret-key-for-jwt-signing)"
  "DB-URI-MIT-PASSWORT|postgresql(\\+asyncpg)?://[^:@[:space:]]+:[^@[:space:]\$\{]{6,}@"
)

# Adressen dagegen ohne den Testordner: dort stehen bewusst RFC1918-Beispiele,
# mit denen die Leck-Erkennung selbst geprueft wird.
ADDRESS_PATTERNS=(
  "PRIVATE-IP|\\b(10\\.[0-9]{1,3}|192\\.168|172\\.(1[6-9]|2[0-9]|3[01]))\\.[0-9]{1,3}\\.[0-9]{1,3}\\b"
  "INTERNE-DOMAIN|[a-z0-9-]+\\.(llm-home|entwicklungsserver)\\.[a-z.]+"
)
PLACEHOLDER_RE='(change[-_]?me|your[-_]?(key|password|token|secret)|example|placeholder|dummy|xxxxxxxx|\*\*\*|<[^>]*>|\$\{|\$\(|REPLACE|TODO|openssl rand|:\?|:-)'

SECRET_HITS=0
scan_patterns() {
  local -n _patterns=$1; shift
  for entry in "${_patterns[@]}"; do
    local label="${entry%%|*}" pattern="${entry#*|}" hits
    hits=$(grep -rInE "$pattern" "${COMMON_EXCL[@]}" --exclude='.env' --exclude='*.override.yml' \
           "$@" "${PROJECT_ROOT}" 2>/dev/null | grep -viE "$PLACEHOLDER_RE" || true)
    if [[ -n "$hits" ]]; then
      fail "${label}:"
      echo "$hits" | head -6 | cut -c1-160 | sed 's|^|        |'
      SECRET_HITS=$((SECRET_HITS + 1))
    fi
  done
}

scan_patterns SECRET_PATTERNS
scan_patterns ADDRESS_PATTERNS --exclude-dir='tests'
if [[ $SECRET_HITS -eq 0 ]]; then
  info "Keine Secrets oder internen Adressen im Code."
else
  fail "${SECRET_HITS} Kategorie(n) mit Funden."
  OVERALL_EXIT=1
fi

# ── 2. Hardcodierte Konfiguration ────────────────────────────────────────────
header "=== 2/4 Hardcodierte Konfiguration ==="

CONFIG_HITS=0
check_config() {
  local label="$1" pattern="$2"
  local hits
  # Kommentar- und Docstring-Zeilen ausblenden — dort steht Prosa, kein Code.
  hits=$(grep -rInE "$pattern" --include='*.py' "${COMMON_EXCL[@]}" \
         --exclude-dir='tests' "${PROJECT_ROOT}" 2>/dev/null \
         | grep -v 'config\.py' \
         | grep -vE '^[^:]+:[0-9]+:[[:space:]]*(#|"""|'"'''"'|\*)' || true)
  if [[ -n "$hits" ]]; then
    warn "${label}:"
    echo "$hits" | head -5 | cut -c1-150 | sed 's|^|        |'
    CONFIG_HITS=$((CONFIG_HITS + 1))
  fi
}

# Absolute Laufzeitpfade und direkte Env-Zugriffe gehoeren in config.py.
check_config "Absolute Pfade im Code" '"/(data|chroma|var|srv)[a-zA-Z0-9/_-]*"'
check_config "Direkter os.getenv/os.environ statt Settings" 'os\.(getenv|environ)'

if [[ $CONFIG_HITS -eq 0 ]]; then
  info "Keine hardcodierten Konfigurationswerte ausserhalb von config.py."
else
  warn "${CONFIG_HITS} Kategorie(n) — pruefen, ob das in die Settings gehoert."
fi

# ── 3. SAST: Bandit ──────────────────────────────────────────────────────────
header "=== 3/4 SAST: Bandit ==="
if ! command -v bandit &>/dev/null; then
  warn "bandit nicht gefunden — installiere via pip..."
  pip install --quiet bandit 2>/dev/null || { warn "Installation fehlgeschlagen, uebersprungen."; }
fi

if command -v bandit &>/dev/null; then
  BANDIT_EXCLUDE="${PROJECT_ROOT}/.venv,${PROJECT_ROOT}/tests,${PROJECT_ROOT}/node_modules,${PROJECT_ROOT}/site"
  if bandit -r "${PROJECT_ROOT}" --exclude "${BANDIT_EXCLUDE}" -ll -q -f txt; then
    info "Bandit: keine Befunde ab Schweregrad MEDIUM."
  else
    fail "Bandit meldet Sicherheitsprobleme (siehe oben)."
    OVERALL_EXIT=1
  fi
else
  warn "Bandit uebersprungen."
fi

# ── 4. SCA: Trivy ────────────────────────────────────────────────────────────
header "=== 4/4 SCA: Trivy (${SEVERITY}) ==="
if [[ "${SKIP_TRIVY:-0}" == "1" ]]; then
  warn "SKIP_TRIVY=1 — uebersprungen."
elif ! command -v docker &>/dev/null; then
  warn "Docker nicht verfuegbar — Trivy uebersprungen (SKIP_TRIVY=1 zum Unterdruecken)."
else
  if docker run --rm -v "${PROJECT_ROOT}:/app:ro" aquasec/trivy fs /app \
       --exit-code 1 --severity "${SEVERITY}" --scanners vuln --quiet; then
    info "Trivy: keine ${SEVERITY}-Schwachstellen."
  else
    fail "Trivy meldet ${SEVERITY}-Schwachstellen in Abhaengigkeiten."
    OVERALL_EXIT=1
  fi
fi

# ── Zusammenfassung ──────────────────────────────────────────────────────────
echo ""
if [ "${OVERALL_EXIT}" -eq 0 ]; then
  info "${BOLD}Alle Sicherheitspruefungen bestanden.${NC}"
else
  fail "${BOLD}Mindestens eine Pruefung ist fehlgeschlagen — vor der Veroeffentlichung beheben.${NC}"
fi
exit "${OVERALL_EXIT}"
