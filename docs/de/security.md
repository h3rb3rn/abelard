# Sicherheitshinweise

## Bekannte Sicherheitslücken & Maßnahmen

### 1. Hardcoded Credentials (HIGH)
**Standort:** `config.py` Zeile ~30  
**Problem:** Standard-Passwörter als Default-Werte (`neo4jpassword`, `debateengine123`)  
**Status:** Kritisch — diese Defaults müssen entfernt werden, kein Fallback auf harte Werte.

```python
# BEFEHL: config.py muss so geändert werden
NEO4J_PASSWORD: str = ...  # Kein Default — immer über .env
POSTGRES_PASSWORD: str = ...  # Kein Default — immer ueber .env
```

### 2. Fehlende Authentifizierung (HIGH)
**Problem:** Keine API-Keys, OAuth oder JWT auf Endpunkten  
**Konsequenz:** Jeder im Netzwerk kann Projekte erstellen und Debatten starten  
**Empfohlen:** API-Key-Basic-Auth Header `X-API-Key` mit Rate-Limiting

### 3. Verschlüsselte Secrets (MEDIUM)
**Problem:** OPENAI_API_KEY in `.env` als Plaintext  
**Empfohlen:** Secret-Vault (HashiCorp Vault, AWS Secrets Manager) für Produktion

### 4. SearXNG Konfiguration (LOW)
**Standort:** `searxng/settings.yml`  
**Problem:** Standard-Engines ohne Rate-Limiting  
**Empfohlen:** Limiter aktivieren über `limiter.toml` mit IP-basierter Begrenzung

## Best Practices für Development

1. **Niemals Secrets committen** — `.env` in `.gitignore`
2. **`.env.example` ohne echte Keys** — als Template verwenden
3. **Docker-Secrets** für production deployments statt Environment-Variablen
4. **Network-Isolation** über Docker Networks — keine unnötigen Host-Mappings
