"""i18n-Modul: Lädt Sprachdateien aus i18n/{locale}/messages.json mit Cache."""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Pfade zu den Sprachdateien
_I18N_DIR = Path(__file__).resolve().parent


class _LocaleCache:
    """Einfacher LRU-Cache pro Locale."""

    def __init__(self, maxsize: int = 4) -> None:
        self._cache: dict[str, dict[str, str]] = {}
        self._maxsize = maxsize

    def get(self, locale: str) -> Optional[dict[str, str]]:
        return self._cache.get(locale)

    def set(self, locale: str, data: dict[str, str]) -> None:
        if len(self._cache) >= self._maxsize and locale not in self._cache:
            oldest = next(iter(self._cache))
            del self._cache[oldest]
        self._cache[locale] = data

    def has(self, locale: str) -> bool:
        return locale in self._cache


_cache = _LocaleCache()


def get(key: str, locale: str = "de") -> str:
    """Gibt den uebersetzten Text fuer einen Key zurueck.

    Args:
        key: Punktierte Taste (z.B. 'debate.started').
        locale: Zwei-Buchstaben-Sprachcode (de/en).

    Returns:
        Uebersetzter Text. Falls Key fehlt: Return key selbst als Fallback.
    """
    parts = key.split(".")
    data = _cache.get(locale) or _load_locale(locale)

    for part in parts:
        if isinstance(data, dict) and part in data:
            data = data[part]
        else:
            # Fallback: Return den original-Key
            return key

    if isinstance(data, str):
        return data

    return key


def _load_locale(locale: str) -> dict[str, str]:
    """Lädt eine Sprachdatei und cacht sie."""
    path = _I18N_DIR / locale / "messages.json"
    if not path.exists():
        logger.warning("Sprachdatei nicht gefunden: %s", path)
        return {}

    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Fehler beim Laden von %s: %s", path, exc)
        return {}

    _cache.set(locale, data)
    return data
