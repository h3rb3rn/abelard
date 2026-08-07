# Veröffentlichung auf GitHub

Zwei Skripte unter `scripts/` bereiten die Veröffentlichung vor und verhindern,
dass Geheimnisse oder deployment-spezifische Werte im öffentlichen Repo landen.

## Ablauf

```bash
bash scripts/run_security_scan.sh          # 1. Codebase prüfen
bash scripts/sync-to-publish.sh --dry-run  # 2. Sync simulieren
bash scripts/sync-to-publish.sh            # 3. Sync ausführen (bricht bei Funden ab)
```

## `run_security_scan.sh`

Prüft das Arbeitsverzeichnis in vier Stufen:

| Stufe | Inhalt |
|-------|--------|
| Secrets | API-Schlüssel, JWTs, private Schlüssel, DB-URIs mit Passwort, private IPs, interne Domains |
| Config | absolute Laufzeitpfade und direkte `os.getenv`-Zugriffe außerhalb von `config.py` |
| SAST | Bandit (statische Python-Analyse, ab Schweregrad MEDIUM) |
| SCA | Trivy (Abhängigkeits-Schwachstellen, via Docker) |

Optionen: `SEVERITY=MEDIUM,HIGH,CRITICAL`, `SKIP_TRIVY=1` (ohne Docker).
Exit-Code 1 bei Funden — damit als CI-Gate nutzbar.

## `sync-to-publish.sh`

1. **rsync** ins Publish-Repo, ohne `.env`, `docker-compose.override.yml`,
   Laufzeitdaten, Caches, Assistenten-Kontext und Zertifikate
2. **Pfade neutralisieren** — Deployment-Pfade werden auf einen generischen Pfad umgeschrieben
3. **Interne Adressen prüfen** — RFC1918-IPs, interne Domains, bekannte Deployment-Hosts
4. **Zugangsdaten prüfen** — dieselben Muster wie im Security-Scan
5. **Streuner prüfen** — findet versehentlich mitkopierte `.env`, Schlüssel, Backups

Bei Funden bricht das Skript mit Exit-Code 1 ab. `--force` überschreibt das
bewusst; `--dry-run` verändert nichts. Ziel- und Quellverzeichnis lassen sich
über `PUB_DIR` und `DEV_DIR` setzen.

!!! warning "Reihenfolge der rsync-Regeln"
    In rsync gewinnt die **erste** passende Regel. Die `--include`-Zeilen für die
    `.example`-Vorlagen stehen deshalb bewusst **vor** den `--exclude`-Mustern —
    sonst schluckt `--exclude='.env.*'` die Datei `.env.example`.

## Was bewusst nicht veröffentlicht wird

| Datei/Ordner | Grund |
|--------------|-------|
| `.env` | echte Zugangsdaten |
| `docker-compose.override.yml` | deployment-spezifische Hosts und Ports |
| `searxng/settings.yml` | Instanz-Konfiguration mit Secret Key |
| `data/`, `chroma-data/` | Laufzeitdaten und Nutzerinhalte |
| `.claude/`, `.codex/`, `.agents/` | Assistenten-Kontext |
| `docs/beispiel-auswertung.md` | Ausgabe einer realen Debatte |

Für jede ausgeschlossene Konfigurationsdatei existiert eine `.example`-Vorlage.

## Erstmalige Einrichtung des Publish-Repos

```bash
mkdir -p /opt/deployment/Github/abelard
cd /opt/deployment/Github/abelard
git init && git remote add origin git@github.com:<user>/abelard.git
```
