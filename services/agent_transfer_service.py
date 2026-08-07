"""Export und Import von Agenten als versioniertes JSON.

Zwei Betriebsarten:

- **portable** (Standard) — nur die Persona selbst: Name, System-Prompt, Biografie,
  Temperatur und Werkzeug-Schalter. Deployment-spezifische Felder (Modell, Basis-URL,
  SearXNG-Adresse) werden weggelassen, weil sie auf einer anderen Installation
  falsch oder — bei internen Adressen — sogar schaedlich waeren. Diese Form eignet
  sich fuer Seed-Dateien im Repository.
- **vollstaendig** — zusaetzlich die LLM-Zuordnung. Fuer Sicherungen der eigenen
  Installation gedacht, nicht zum Weitergeben.

API-Schluessel sind in keiner Variante enthalten: Sie haengen am
``UserLLMEndpoint``, nicht am Agenten.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_IMPORT_AGENTS = 500

# Felder, die eine Persona ausmachen — immer enthalten.
PORTABLE_FIELDS = (
    "name",
    "system_prompt",
    "persona_bio",
    "temperature",
    "web_search_enabled",
    "web_search_provider",
    "knowledge_graph_enabled",
    "cache_enabled",
    "mcp_enabled",
)

# Felder, die nur zur konkreten Installation gehoeren.
DEPLOYMENT_FIELDS = ("llm_provider", "llm_base_url", "llm_model", "searxng_url")

_MAX_LEN = {"name": 128, "system_prompt": 20000, "persona_bio": 8000}

# Adressen, die niemals in eine portable Datei gehoeren.
_PRIVATE_HOST_RE = re.compile(
    r"(https?://)?("
    r"10(\.\d{1,3}){3}|192\.168(\.\d{1,3}){2}|172\.(1[6-9]|2\d|3[01])(\.\d{1,3}){2}"
    r"|localhost|127\.0\.0\.1"
    r")",
    re.IGNORECASE,
)


class ImportValidationError(ValueError):
    """Das übergebene JSON ist kein gültiges Agenten-Bündel."""


@dataclass
class ImportResult:
    created: list[str]
    skipped: list[str]
    replaced: list[str]
    rejected: list[dict[str, str]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": len(self.created),
            "skipped": len(self.skipped),
            "replaced": len(self.replaced),
            "rejected": len(self.rejected),
            "created_names": self.created,
            "skipped_names": self.skipped,
            "replaced_names": self.replaced,
            "rejected_details": self.rejected,
        }


def agent_to_dict(agent: Any, portable: bool = True) -> dict[str, Any]:
    """Serialisiert einen Agenten. ``portable`` laesst installationsspezifische Felder weg."""
    data: dict[str, Any] = {
        "name": agent.name,
        "system_prompt": agent.system_prompt or "",
        "persona_bio": agent.persona_bio,
        "temperature": float(agent.temperature if agent.temperature is not None else 0.7),
        "web_search_enabled": bool(agent.web_search_enabled),
        "web_search_provider": agent.web_search_provider or "duckduckgo",
        "knowledge_graph_enabled": bool(agent.knowledge_graph_enabled),
        "cache_enabled": bool(agent.cache_enabled),
        "mcp_enabled": bool(agent.mcp_enabled),
    }
    if not portable:
        data.update({
            "llm_provider": agent.llm_provider or "openai",
            "llm_base_url": agent.llm_base_url,
            "llm_model": agent.llm_model,
            "searxng_url": agent.searxng_url,
        })
    return data


def build_bundle(agents: list[Any], portable: bool = True, source: str = "") -> dict[str, Any]:
    """Erzeugt das Export-Buendel inklusive Metadaten."""
    return {
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "portable": portable,
        "source": source,
        "count": len(agents),
        "agents": [agent_to_dict(a, portable=portable) for a in agents],
    }


def contains_private_address(bundle: dict[str, Any]) -> list[str]:
    """Findet interne Adressen im Buendel — Schutz vor versehentlicher Weitergabe."""
    hits: list[str] = []
    for agent in bundle.get("agents", []):
        for field in ("llm_base_url", "searxng_url"):
            value = agent.get(field)
            if value and _PRIVATE_HOST_RE.search(str(value)):
                hits.append(f"{agent.get('name', '?')}.{field}={value}")
    return hits


def parse_bundle(payload: Any) -> list[dict[str, Any]]:
    """Validiert ein Buendel und liefert die bereinigten Agentendaten.

    Akzeptiert sowohl das vollstaendige Buendel als auch eine blanke Liste von
    Agenten — Letzteres, weil Nutzer erfahrungsgemaess Teilausschnitte einfuegen.
    """
    if isinstance(payload, list):
        raw_agents, version = payload, SCHEMA_VERSION
    elif isinstance(payload, dict):
        raw_agents = payload.get("agents")
        version = payload.get("schema_version", SCHEMA_VERSION)
        if not isinstance(raw_agents, list):
            raise ImportValidationError("Feld 'agents' fehlt oder ist keine Liste")
    else:
        raise ImportValidationError("Erwartet wird ein JSON-Objekt oder eine JSON-Liste")

    if not isinstance(version, int) or version > SCHEMA_VERSION:
        raise ImportValidationError(
            f"schema_version {version} wird nicht unterstuetzt (maximal {SCHEMA_VERSION})"
        )
    if not raw_agents:
        raise ImportValidationError("Das Buendel enthaelt keine Agenten")
    if len(raw_agents) > MAX_IMPORT_AGENTS:
        raise ImportValidationError(f"Zu viele Agenten ({len(raw_agents)}, maximal {MAX_IMPORT_AGENTS})")

    cleaned: list[dict[str, Any]] = []
    for idx, entry in enumerate(raw_agents, start=1):
        if not isinstance(entry, dict):
            raise ImportValidationError(f"Eintrag {idx} ist kein Objekt")
        name = str(entry.get("name", "")).strip()
        if not name:
            raise ImportValidationError(f"Eintrag {idx} hat keinen Namen")
        cleaned.append(_clean_entry(entry, name))
    return cleaned


def _clean_entry(entry: dict[str, Any], name: str) -> dict[str, Any]:
    """Uebernimmt nur bekannte Felder und kappt zu lange Texte."""
    out: dict[str, Any] = {"name": name[: _MAX_LEN["name"]]}

    prompt = str(entry.get("system_prompt") or "").strip()
    out["system_prompt"] = prompt[: _MAX_LEN["system_prompt"]] or f"Du bist {name}, ein Debattenteilnehmer."

    bio = entry.get("persona_bio")
    out["persona_bio"] = str(bio).strip()[: _MAX_LEN["persona_bio"]] if bio else None

    try:
        temp = float(entry.get("temperature", 0.7))
    except (TypeError, ValueError):
        temp = 0.7
    out["temperature"] = min(max(temp, 0.0), 2.0)

    for flag in ("web_search_enabled", "knowledge_graph_enabled", "cache_enabled", "mcp_enabled"):
        out[flag] = bool(entry.get(flag, False))

    provider = str(entry.get("web_search_provider") or "duckduckgo").lower()
    out["web_search_provider"] = provider if provider in {"duckduckgo", "searxng"} else "duckduckgo"

    for field in DEPLOYMENT_FIELDS:
        if field in entry and entry[field]:
            out[field] = str(entry[field])[:512]
    return out
