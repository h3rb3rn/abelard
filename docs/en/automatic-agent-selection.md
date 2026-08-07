# Automatic Agent Selection

Projects can either have participants manually assigned (`manual`) or **automatically selected by AI** based on the motion topic (`auto`).

## How Selection Works

1. **Candidate Pool:** Includes all user-owned agents plus all globally shared agents. Previously auto-cloned project agents are excluded to prevent self-referential loops.
2. **Analysis:** The motion and a compact agent catalog are sent to the **user's default LLM endpoint** (`is_default: true` in `user_llm_endpoints`) with `temperature=0.2` for deterministic selection.
3. **Criteria:** Domain expertise relevance, *opposing philosophical/methodological stances*, and topical breadth. A panel of uniform thinkers produces no real debate.
4. **Materialization:** For each selected candidate, a **project clone** is created with full tool permissions enabled. Original agents remain untouched.

### Full Tool Permissions

Automatically selected agents receive:

| Permission | Setting |
|------------|---------|
| `web_search_enabled` | `true` |
| `web_search_provider` | SearXNG (or original provider) |
| `searxng_url` | Inherited or `SEARXNG_BASE_URL` |
| `knowledge_graph_enabled` | `true` |
| `cache_enabled` | `true` |
| `mcp_enabled` | `true` |

Clones store `skills_json = {"auto_assigned": true, "selection_reason": "..."}`. Re-running selection deletes previous automated project clones to prevent duplicate accumulations.

!!! note "Fallback Heuristic"
    If the LLM endpoint fails or produces malformed JSON, a deterministic keyword overlap heuristic (Motion terms vs Agent profile) fills the panel. Debate execution never fails due to selection errors.

## API Endpoints

```
POST /api/v2/projects                       # Specify agent_selection_mode and auto_agent_count
PATCH /api/v2/projects/{id}                 # Update selection parameters
POST /api/v2/projects/{id}/suggest-agents            # Preview selection (side-effect free)
POST /api/v2/projects/{id}/suggest-agents?apply=true # Materialize selection
POST /api/v2/projects/{id}/suggest-agents?count=6    # Override participant count
```
