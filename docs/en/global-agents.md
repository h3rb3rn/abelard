# Globally Shared Agents

Agents are normally owned by a single user (tenant isolation). An **Administrator** can grant global visibility to their agents — making them accessible to all registered users while retaining original ownership.

## Permissions Model

| Action | Agent Owner | Admin (Owner) | External User |
|--------|-------------|---------------|---------------|
| View | ✅ | ✅ | ✅ (If global) |
| Edit / Delete | ✅ | ✅ | ❌ (404 Not Found) |
| Toggle Global Sharing | ❌ | ✅ | ❌ (403 Forbidden) |
| Clone into Own Scope | — | — | ✅ |

External users cannot modify global agents owned by others. They can clone a global agent into their private account (`is_global = false`), creating an editable local copy.

!!! warning "Project Assignment Auto-Clones"
    An agent stores a single `project_id` reference. Directly assigning another user's global agent to a project would transfer ownership out of the creator's account. `_assign_agents_to_project()` detects cross-tenant assignments and transparently clones the agent into the target project instead.

## Data Schema

- `users.is_admin` (BOOLEAN, default `false`) — Grants global sharing privileges.
- `agents.is_global` (BOOLEAN, default `false`) — Exposes agent across all tenants.

## API Endpoints

```
GET    /api/v2/agents?scope=all      # Owned + global agents (default)
GET    /api/v2/agents?scope=own      # User's owned agents only
GET    /api/v2/agents?scope=global   # Global agents only
PATCH  /api/v2/agents/{id}/global    # Toggle global status {"is_global": true|false} (Admin only)
POST   /api/v2/agents/{id}/clone     # Clone global agent into private account
POST   /api/v2/agents/seed-personas?make_global=true   # Seed personas globally (Admin only)
```

## Granting Admin Privileges

Execute against PostgreSQL:

```sql
UPDATE users SET is_admin = TRUE WHERE email = 'admin@example.org';
```
