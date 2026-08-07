"""Service: Online-Websuche über DuckDuckGo und SearXNG für Agenten."""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import List, Dict, Any
import httpx

from dataclasses import dataclass

from config import settings

logger = logging.getLogger(__name__)

# Zentral aus den Settings, nicht aus einer eigenen Env-Variablen: frueher las
# dieses Modul SEARXNG_URL, waehrend der Rest der Anwendung SEARXNG_BASE_URL
# verwendet — eine gesetzte Konfiguration wirkte hier schlicht nicht.
SEARXNG_DEFAULT_URL = settings.searxng_base_url


@dataclass
class SearchResult:
    title: str
    snippet: str
    url: str


class SearchService:
    """Wrapper class for Orchestrator compatibility."""
    def __init__(self, provider: str = "duckduckgo", searxng_url: str = ""):
        self.provider = provider
        self.searxng_url = searxng_url

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        res = await perform_web_search(query, provider=self.provider, searxng_url=self.searxng_url, max_results=max_results)
        return [SearchResult(title=r["title"], snippet=r["snippet"], url=r["url"]) for r in res.get("results", [])]

    async def close(self):
        pass


async def search_duckduckgo(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Durchsucht DuckDuckGo nach aktuellen Informationen und gibt Titel, Snippet & URL zurück."""
    results: List[Dict[str, str]] = []
    
    # 1. DuckDuckGo Instant Answer API
    try:
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
            url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1&skip_disambig=1"
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                abstract = data.get("AbstractText", "").strip()
                heading = data.get("Heading", "").strip()
                abstract_url = data.get("AbstractURL", "").strip()

                if abstract:
                    results.append({
                        "title": heading or query,
                        "snippet": abstract,
                        "url": abstract_url or "https://duckduckgo.com"
                    })

                for topic in data.get("RelatedTopics", []):
                    if isinstance(topic, dict) and "Text" in topic:
                        results.append({
                            "title": topic.get("FirstURL", "").split("/")[-1].replace("_", " "),
                            "snippet": topic.get("Text", ""),
                            "url": topic.get("FirstURL", "")
                        })
                    if len(results) >= max_results:
                        break
    except Exception as exc:
        logger.warning("DuckDuckGo Instant Answer API failed: %s", exc)

    # 2. DuckDuckGo Lite HTML Search Fallback if Instant Answer returned few results
    if len(results) < max_results:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            async with httpx.AsyncClient(timeout=6.0, follow_redirects=True, headers=headers) as client:
                resp = await client.post("https://html.duckduckgo.com/html/", data={"q": query})
                if resp.status_code == 200:
                    html_content = resp.text
                    # Extract snippets & links via regex
                    snippet_matches = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', html_content, re.DOTALL)
                    title_matches = re.findall(r'<a class="result__url"[^>]*>(.*?)</a>', html_content, re.DOTALL)
                    
                    for i in range(min(len(snippet_matches), max_results - len(results))):
                        clean_snippet = re.sub(r'<[^>]+>', '', snippet_matches[i]).strip()
                        clean_title = re.sub(r'<[^>]+>', '', title_matches[i]).strip() if i < len(title_matches) else query
                        if clean_snippet:
                            results.append({
                                "title": clean_title or f"Result {i+1}",
                                "snippet": clean_snippet,
                                "url": f"https://duckduckgo.com/html/?q={urllib.parse.quote(query)}"
                            })
        except Exception as exc:
            logger.warning("DuckDuckGo Lite Search fallback failed: %s", exc)

    return results[:max_results]


async def search_searxng(query: str, searxng_url: str = "", max_results: int = 5) -> List[Dict[str, str]]:
    """Durchsucht eine SearXNG-Instanz nach aktuellen Ergebnisse."""
    results: List[Dict[str, str]] = []
    base_url = (searxng_url or SEARXNG_DEFAULT_URL).rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
            target_url = f"{base_url}/search?q={urllib.parse.quote(query)}&format=json"
            r = await client.get(target_url)
            if r.status_code == 200:
                data = r.json()
                for item in data.get("results", []):
                    title = item.get("title", "").strip()
                    snippet = item.get("content", "").strip()
                    url = item.get("url", "").strip()

                    if snippet:
                        results.append({
                            "title": title or query,
                            "snippet": snippet,
                            "url": url
                        })
                    if len(results) >= max_results:
                        break
            else:
                logger.warning("SearXNG HTTP %s from %s", r.status_code, base_url)
    except Exception as exc:
        logger.warning("SearXNG search failed at %s: %s", base_url, exc)

    return results[:max_results]


async def perform_web_search(
    query: str,
    provider: str = "duckduckgo",
    searxng_url: str = "",
    max_results: int = 5
) -> Dict[str, Any]:
    """Execute web search using the specified provider ('duckduckgo' or 'searxng')."""
    provider = (provider or "duckduckgo").lower()
    
    if provider == "searxng":
        results = await search_searxng(query, searxng_url=searxng_url, max_results=max_results)
        if not results: # Fallback to DuckDuckGo if SearXNG is unreachable
            results = await search_duckduckgo(query, max_results=max_results)
    else:
        results = await search_duckduckgo(query, max_results=max_results)

    # Format search results into a clean string for LLM injection
    if not results:
        formatted_text = f"Keine Online-Ergebnisse für '{query}' gefunden."
    else:
        lines = [f"=== WEB-SUCHERGEBNISSE FÜR '{query}' ({provider.upper()}) ==="]
        for idx, res in enumerate(results, start=1):
            lines.append(f"[{idx}] {res['title']}\n    Snippet: {res['snippet']}\n    Quelle: {res['url']}")
        formatted_text = "\n".join(lines)

    return {
        "query": query,
        "provider": provider,
        "count": len(results),
        "results": results,
        "formatted_text": formatted_text
    }