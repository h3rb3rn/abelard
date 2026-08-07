# Global freigegebene Agenten

Agenten gehören normalerweise genau einem Benutzer (Mandanten-Isolation). Ein
**Admin** kann eigene Agenten zusätzlich *global* freigeben — sie werden dann für
alle registrierten Benutzer sichtbar und nutzbar, bleiben aber im Besitz des Admins.

## Rechtemodell

| Aktion | Eigentümer | Admin (Eigentümer) | Anderer Benutzer |
|--------|-----------|--------------------|------------------|
| Sehen | ✅ | ✅ | ✅ nur wenn global |
| Bearbeiten / Löschen | ✅ | ✅ | ❌ (404) |
| Global freigeben | ❌ | ✅ | ❌ (403) |
| Übernehmen (klonen) | — | — | ✅ |

Nicht-Eigentümer können einen globalen Agenten **nicht** verändern. Sie können ihn
als eigene Kopie übernehmen; die Kopie ist privat (`is_global = false`) und
vollständig bearbeitbar.

!!! warning "Projektzuweisung klont automatisch"
    Ein Agent hat nur ein `project_id`-Feld. Würde ein fremder Benutzer einen
    globalen Agenten direkt seinem Projekt zuweisen, würde der Agent aus dem Konto
    des Admins herausgezogen. `_assign_agents_to_project()` erkennt das und legt
    stattdessen eine Kopie im Zielmandanten an — das Original bleibt unangetastet.

## Datenmodell

- `users.is_admin` (BOOLEAN, Default `false`) — darf global freigeben
- `agents.is_global` (BOOLEAN, Default `false`) — für alle sichtbar

Beide Spalten werden beim Start über `ALTER TABLE … ADD COLUMN IF NOT EXISTS`
ergänzt, eine separate Migration ist nicht nötig.

## API

```
GET    /api/v2/agents?scope=all      # eigene + globale (Default)
GET    /api/v2/agents?scope=own      # nur eigene
GET    /api/v2/agents?scope=global   # nur global freigegebene
PATCH  /api/v2/agents/{id}/global    # {"is_global": true|false} — nur Admin
POST   /api/v2/agents/{id}/clone     # globalen Agenten übernehmen
POST   /api/v2/agents/seed-personas?make_global=true   # Bibliothek direkt global anlegen
```

Jeder Agent in der Antwort trägt `is_global` und `is_owner`, sodass die
Oberfläche Aktionen korrekt einschränken kann.

Beispiel — Persona-Bibliothek als Admin global bereitstellen:

```bash
curl -X POST "http://localhost:8106/api/v2/agents/seed-personas?make_global=true" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

## Dashboard

Auf der Agentenseite zeigt die Kopfzeile, wie viele Agenten eigene und wie viele
global verfügbar sind. Globale Agenten tragen ein violettes Badge (*🌍 Global
freigegeben* beim Eigentümer, *🌍 Global (fremd)* bei anderen). Admins sehen bei
eigenen Agenten einen Umschalter, alle anderen bei fremden globalen Agenten den
Button **📥 Übernehmen**.

## Einen Benutzer zum Admin machen

```sql
UPDATE users SET is_admin = TRUE WHERE email = 'admin@sovereign.local';
```
