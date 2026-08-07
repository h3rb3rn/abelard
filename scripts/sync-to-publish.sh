#!/usr/bin/env bash
# =============================================================================
#  sync-to-publish.sh — Abelard: Dev-Repo → GitHub-Repo
#
#  Usage:
#    bash scripts/sync-to-publish.sh [--dry-run] [--force]
#
#  Kopiert den Code aus dem Entwicklungsverzeichnis in das Veroeffentlichungs-
#  Repo, laesst dabei Geheimnisse, Laufzeitdaten und lokale Konfiguration aus,
#  bereinigt deployment-spezifische Pfade und prueft anschliessend auf
#  ausgetretene Zugangsdaten, IPs und interne Hostnamen.
#
#  Bei Funden bricht das Skript mit Exit-Code 1 ab — ausser mit --force.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEV_DIR="${DEV_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
PUB_DIR="${PUB_DIR:-/opt/deployment/Github/abelard}"

DRY_RUN=false
FORCE=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --force)   FORCE=true ;;
    *) echo "[ERROR] Unbekannte Option: $arg"; exit 2 ;;
  esac
done
$DRY_RUN && echo "[DRY RUN] Es werden keine Dateien veraendert."

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $*"; }
bad()  { echo -e "  ${RED}✗${NC} $*"; }

[[ -d "$DEV_DIR" ]] || { echo "[ERROR] Dev-Verzeichnis fehlt: $DEV_DIR"; exit 1; }
if [[ ! -d "$PUB_DIR" ]]; then
  echo "[ERROR] Publish-Verzeichnis fehlt: $PUB_DIR"
  echo "        Anlegen mit:  mkdir -p '$PUB_DIR' && git -C '$PUB_DIR' init"
  exit 1
fi

echo "════════════════════════════════════════════════════"
echo "  Abelard — Sync: DEV → PUBLISH"
echo "  Von:  $DEV_DIR"
echo "  Nach: $PUB_DIR"
echo "════════════════════════════════════════════════════"

# ─── Schritt 1: rsync ─────────────────────────────────────────────────────
# ACHTUNG: In rsync gewinnt die ERSTE passende Regel. Die --include-Regeln fuer
# die Vorlagen muessen deshalb VOR den zugehoerigen --exclude-Mustern stehen,
# sonst schluckt z.B. '.env.*' die Datei '.env.example'.
RSYNC_EXCLUDES=(
  --include='.env.example'
  --include='docker-compose.override.yml.example'
  --exclude='.git'
  --exclude='.env'
  --exclude='.env.*'
  --exclude='docker-compose.override.yml'
  --exclude='__pycache__'
  --exclude='*.pyc'
  --exclude='.pytest_cache/'
  --exclude='.mypy_cache/'
  --exclude='.ruff_cache/'
  --exclude='node_modules'
  --exclude='.claude/'
  --exclude='.codex/'
  --exclude='.agents/'
  --exclude='*.log'
  --exclude='*.db'
  --exclude='*.sqlite*'
  --exclude='*.pem'
  --exclude='*.key'
  --exclude='*.crt'
  --exclude='*.cert'
  --exclude='*.p12'
  --exclude='data/'
  --exclude='chroma-data/'
  --exclude='site/'
  --exclude='.vscode/'
  --exclude='.idea/'
  --exclude='*.bak'
  --exclude='tmp_*'
  --exclude='searxng/settings.yml'
  --exclude='docs/beispiel-auswertung.md'
)

echo ""
echo "[1/5] Dateien synchronisieren..."
if $DRY_RUN; then
  rsync -avn --delete "${RSYNC_EXCLUDES[@]}" "$DEV_DIR/" "$PUB_DIR/" | tail -25
else
  rsync -a --delete "${RSYNC_EXCLUDES[@]}" "$DEV_DIR/" "$PUB_DIR/"
  ok "Dateien synchronisiert"
fi

# ─── Schritt 2: deployment-spezifische Pfade neutralisieren ───────────────
echo "[2/5] Pfade neutralisieren..."
if $DRY_RUN; then
  echo "  (im Dry-Run uebersprungen)"
else
  # Aus dem echten DEV_DIR ableiten statt fest zu verdrahten: sonst laeuft die
  # Ersetzung ins Leere, sobald das Arbeitsverzeichnis umbenannt wird.
  DEV_PARENT="$(dirname "$DEV_DIR")"
  find "$PUB_DIR" \( -name '*.py' -o -name '*.sh' -o -name '*.md' -o -name '*.yml' -o -name '*.toml' \) \
    -not -path '*/.git/*' -exec \
    sed -i -e "s|${DEV_DIR}|/opt/abelard|g" \
           -e "s|${DEV_PARENT}|/opt/abelard|g" {} + 2>/dev/null || true
  ok "Pfade neutralisiert (${DEV_DIR} → /opt/abelard)"
fi

# ─── Schritt 3: interne Adressen ──────────────────────────────────────────
echo "[3/5] Interne Adressen pruefen..."
FINDINGS=0

# Private IPv4-Bereiche (RFC1918) und die bekannten oeffentlichen Deployment-Hosts
ADDR_PATTERNS=(
  "PRIVATE-IP|\\b(10\\.[0-9]{1,3}|192\\.168|172\\.(1[6-9]|2[0-9]|3[01]))\\.[0-9]{1,3}\\.[0-9]{1,3}\\b"
  "INTERNE-DOMAIN|[a-z0-9-]+\\.(llm-home|entwicklungsserver)\\.[a-z.]+"
  "OEFFENTLICHE-IP|\\b84\\.118\\.118\\.[0-9]{1,3}\\b"
)
ADDR_SCAN=(--include='*.py' --include='*.yml' --include='*.yaml' --include='*.json'
           --include='*.sh' --include='*.md' --include='*.toml' --include='*.html'
           --include='Dockerfile*')
ADDR_EXCL=(--exclude-dir='.git' --exclude-dir='node_modules' --exclude-dir='__pycache__' --exclude-dir='tests'
           --exclude='*.example')

for entry in "${ADDR_PATTERNS[@]}"; do
  label="${entry%%|*}"; pattern="${entry#*|}"
  hits=$(grep -rInE "$pattern" "${ADDR_SCAN[@]}" "${ADDR_EXCL[@]}" "$PUB_DIR" 2>/dev/null || true)
  if [[ -n "$hits" ]]; then
    bad "$label gefunden:"
    echo "$hits" | head -8 | sed 's|^|      |'
    FINDINGS=$((FINDINGS + 1))
  fi
done
[[ $FINDINGS -eq 0 ]] && ok "Keine internen Adressen gefunden"

# ─── Schritt 4: Zugangsdaten ──────────────────────────────────────────────
echo "[4/5] Zugangsdaten pruefen..."

SCAN_INCLUDES=(--include='*.py' --include='*.yml' --include='*.yaml' --include='*.json'
               --include='*.sh' --include='*.toml' --include='*.ini' --include='*.conf'
               --include='*.cfg' --include='*.env' --include='*.html' --include='*.md'
               --include='Dockerfile*' --include='docker-compose*.yml')
SCAN_EXCLUDES=(--exclude-dir='.git' --exclude-dir='node_modules' --exclude-dir='__pycache__'
               --exclude-dir='tests' --exclude='*.example' --exclude='*.sample' --exclude='*.template')

readonly CRED_PATTERNS=(
  "OPENAI|\\bsk-[A-Za-z0-9_-]{20,}"
  "ANTHROPIC|\\bsk-ant-[A-Za-z0-9_-]{20,}"
  "MOE-SK|\\bmoe-sk-[a-f0-9]{32,}"
  "AWS-AKIA|(AKIA|ASIA)[A-Z0-9]{16}"
  "AWS-SECRET|aws_secret_access_key[[:space:]]*=[[:space:]]*[A-Za-z0-9/+=]{40}"
  "GH-PAT|gh[opsur]_[A-Za-z0-9]{36,251}"
  "JWT|\\beyJ[A-Za-z0-9_-]{10,}\\.eyJ[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}\\b"
  "PRIVATE-KEY|-----BEGIN[A-Z ]*PRIVATE KEY-----"
  "BEKANNTE-DEFAULTS|[:=][[:space:]]*[\x27\"]?(neo4jpassword|debateengine123|fixed-secret-key-for-jwt-signing)"
  "POSTGRES-URI|postgresql(\\+asyncpg)?://[^:@[:space:]]+:[^@[:space:]\$\{]{6,}@"
  "REDIS-URI|redis://[^:@[:space:]]*:[^@[:space:]\$\{]{6,}@"
  "GENERIC-PASS|^[[:space:]]*[A-Z_][A-Z0-9_]*_(PASS|PASSWORD|SECRET|TOKEN|API_KEY)[[:space:]]*[:=][[:space:]]*[A-Za-z0-9/+=_.!@#%^&*-]{12,}"
)

# Platzhalter und Doku-Beispiele nie melden.
readonly PLACEHOLDER_RE='(change[-_]?me|your[-_]?(key|password|token|secret)|example|placeholder|dummy|changeit|xxxxxxxx|\*\*\*|<[^>]*>|\$\{|\$\(|REPLACE|TODO|openssl rand|:\?|:-)'

SECRET_FINDINGS=0
for entry in "${CRED_PATTERNS[@]}"; do
  label="${entry%%|*}"; pattern="${entry#*|}"
  hits=$(grep -rInE "$pattern" "${SCAN_INCLUDES[@]}" "${SCAN_EXCLUDES[@]}" "$PUB_DIR" 2>/dev/null \
         | grep -viE "$PLACEHOLDER_RE" || true)
  if [[ -n "$hits" ]]; then
    bad "$label gefunden:"
    echo "$hits" | head -6 | cut -c1-160 | sed 's|^|      |'
    SECRET_FINDINGS=$((SECRET_FINDINGS + 1))
  fi
done
[[ $SECRET_FINDINGS -eq 0 ]] && ok "Keine Zugangsdaten gefunden"

# ─── Schritt 5: versehentlich mitkopierte Dateien ─────────────────────────
echo "[5/5] Auf ausgeschlossene Dateien pruefen..."
STRAY=$(find "$PUB_DIR" -not -path '*/.git/*' \
  \( -name '.env' -o -name '*.pem' -o -name '*.key' -o -name '*.db' \
     -o -name 'docker-compose.override.yml' -o -name '*.bak' -o -name 'tmp_*' \) 2>/dev/null || true)
if [[ -n "$STRAY" ]]; then
  bad "Dateien, die nicht veroeffentlicht werden duerfen:"
  echo "$STRAY" | sed 's|^|      |'
  FINDINGS=$((FINDINGS + 1))
else
  ok "Keine ausgeschlossenen Dateien im Publish-Repo"
fi

# ─── Zusammenfassung ──────────────────────────────────────────────────────
TOTAL=$((FINDINGS + SECRET_FINDINGS))
echo ""
echo "════════════════════════════════════════════════════"
if $DRY_RUN; then
  echo "  DRY RUN beendet — nichts veraendert."
  exit 0
fi

FILE_COUNT=$(find "$PUB_DIR" -type f -not -path '*/.git/*' | wc -l)
PUB_SIZE=$(du -sh "$PUB_DIR" --exclude=.git 2>/dev/null | awk '{print $1}')
echo "  Dateien: $FILE_COUNT | Groesse: $PUB_SIZE"

if [[ $TOTAL -gt 0 ]]; then
  echo ""
  bad "$TOTAL Kategorie(n) mit Funden — NICHT veroeffentlichen."
  echo "     Beheben und erneut ausfuehren."
  $FORCE || exit 1
  warn "--force gesetzt: Fehler werden ignoriert."
else
  echo ""
  ok "Alle Pruefungen bestanden."
  echo ""
  echo "  Naechste Schritte:"
  echo "    cd $PUB_DIR"
  echo "    git add -A && git diff --cached --stat"
  echo "    git commit -m 'Sync from dev: <Beschreibung>'"
  echo "    git push origin main"
fi
echo "════════════════════════════════════════════════════"
