"""Application-wide configuration via Pydantic Settings.

Sicherheitsgrundsatz: Es gibt KEINE funktionsfaehigen Credential-Defaults im
Quelltext. Passwoerter und Schluessel kommen ausschliesslich aus der Umgebung
(``.env`` oder echte Env-Variablen). Fehlt ein Geheimnis, wird entweder ein
Zufallswert erzeugt und laut gewarnt oder der Start abgebrochen — je nachdem,
ob ein stiller Fehlbetrieb tolerierbar waere.
"""

from __future__ import annotations

import logging
import secrets

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Werte, die frueher als Default im Quelltext standen. Wer sie noch in seiner
# .env stehen hat, bekommt eine deutliche Warnung.
_KNOWN_WEAK_SECRETS = {
    "neo4jpassword",
    "debateengine123",
    "fixed-secret-key-for-jwt-signing",
    "changeme",
    "password",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Betriebsmodus -----------------------------------------------------------
    # "production" erzwingt gesetzte Geheimnisse statt Zufallswerte.
    environment: str = "development"

    # -- HTTP-Server -------------------------------------------------------------
    # Im Container ist 0.0.0.0 korrekt; ausserhalb sollte auf 127.0.0.1 gebunden
    # und die Freigabe einem Reverse Proxy ueberlassen werden.
    api_host: str = "0.0.0.0"  # noqa: S104 - Container-Bind, per API_HOST ueberschreibbar
    api_port: int = 8000

    # -- LLM Provider selection ------------------------------------------------
    openai_api_key: str = ""
    default_provider: str = "openai"  # "ollama" | "openai"
    ollama_base_url: str = "http://ollama:11430"
    ollama_model: str = "mistral:latest"
    ollama_keep_alive: str = "-1"  # -1 = keep in VRAM indefinitely (prevents 5m auto-unload)
    openai_model: str = "gpt-4o-mini"

    # -- Valkey (IPC / kill-switch / cost) -------------------------------------
    valkey_host: str = "valkey"
    valkey_port: int = 6379
    valkey_password: str = ""

    # -- Neo4j ------------------------------------------------------------------
    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""

    # -- ChromaDB ---------------------------------------------------------------
    chroma_persist_dir: str = "/chroma-data"

    # -- SearXNG ----------------------------------------------------------------
    searxng_base_url: str = "http://searxng:8080"

    # -- Datei-Uploads & Debatten-Logs -------------------------------------------
    upload_dir: str = "/data/uploads"
    upload_max_bytes: int = 20 * 1024 * 1024
    debate_log_dir: str = "/data/debate-logs"

    # -- JWT Authentication ---------------------------------------------------
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24

    # -- PostgreSQL / Database ------------------------------------------------
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str = "debate"
    postgres_password: str = ""
    postgres_db: str = "sovereign_debate"
    db_custom_uri: str = Field(default="", alias="POSTGRES_URI")

    @property
    def postgres_uri(self) -> str:
        if self.db_custom_uri:
            return self.db_custom_uri
        return f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    # -- Moderation / guardrails ------------------------------------------------
    moderator_interval: int = Field(default=3, ge=1)
    cost_threshold_usd: float = Field(default=5.0, gt=0)
    default_temperature: float = Field(default=0.7, ge=0.0, le=2.0)

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod"}

    @property
    def valkey_url(self) -> str:
        if self.valkey_password:
            return f"redis://:{self.valkey_password}@{self.valkey_host}:{self.valkey_port}/0"
        return f"redis://{self.valkey_host}:{self.valkey_port}/0"

    @property
    def searxng_search_url(self) -> str:
        return f"{self.searxng_base_url.rstrip('/')}/search?format=json"

    # -- Validierung ------------------------------------------------------------

    @model_validator(mode="after")
    def _check_secrets(self) -> "Settings":
        """Prueft Geheimnisse auf Fehlen und auf bekannte Beispielwerte.

        In Produktion (``ENVIRONMENT=production``) ist beides ein harter Fehler.
        In der Entwicklung wird gewarnt, damit ein bestehendes Setup nicht
        ploetzlich nicht mehr startet — ein erzwungener Passwortwechsel an einer
        laufenden Datenbank waere schlimmer als die Warnung.
        """
        weak: list[str] = []
        missing: list[str] = []
        for name, value in (
            ("JWT_SECRET", self.jwt_secret),
            ("POSTGRES_PASSWORD", self.postgres_password or self.db_custom_uri),
            ("NEO4J_PASSWORD", self.neo4j_password),
        ):
            if not value:
                missing.append(name)
            elif value.strip().lower() in _KNOWN_WEAK_SECRETS:
                weak.append(name)

        if self.is_production and (missing or weak):
            problems = []
            if missing:
                problems.append(f"nicht gesetzt: {', '.join(missing)}")
            if weak:
                problems.append(f"bekannter Beispielwert: {', '.join(weak)}")
            raise ValueError(
                "ENVIRONMENT=production, aber " + "; ".join(problems) + ". "
                "Setze eigene Werte in der .env (z.B. `openssl rand -hex 32`) — "
                "im Quelltext gibt es bewusst keine Credential-Defaults."
            )

        for name in weak:
            logger.warning(
                "%s verwendet ein bekanntes Beispiel-Passwort. Vor dem Produktivbetrieb "
                "unbedingt aendern (`openssl rand -hex 32`).", name,
            )

        if not self.jwt_secret:
            # Zufaelliger Schluessel je Prozessstart: sicher, aber Tokens gelten
            # nur bis zum Neustart. Deshalb die deutliche Warnung.
            object.__setattr__(self, "jwt_secret", secrets.token_urlsafe(48))
            logger.warning(
                "JWT_SECRET ist nicht gesetzt — es wurde ein Zufallsschluessel erzeugt. "
                "Alle Sitzungen werden beim naechsten Neustart ungueltig. "
                "Setze JWT_SECRET in der .env (z.B. `openssl rand -hex 32`)."
            )
        for name in missing:
            if name != "JWT_SECRET":
                logger.warning("%s ist nicht gesetzt — die Verbindung wird vermutlich fehlschlagen.", name)
        return self


settings = Settings()
