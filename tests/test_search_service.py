"""Tests for search service parsing."""

from unittest.mock import AsyncMock, patch

import pytest

from services.search_service import SearchResult, SearchService, perform_web_search


class TestSearchServiceMapping:
    @pytest.mark.asyncio
    async def test_search_maps_results(self) -> None:
        fake = {
            "query": "test",
            "provider": "searxng",
            "count": 1,
            "results": [{"title": "Test Article", "snippet": "Some content here.", "url": "https://example.com"}],
            "formatted_text": "…",
        }
        with patch("services.search_service.perform_web_search", new=AsyncMock(return_value=fake)):
            svc = SearchService(provider="searxng", searxng_url="http://searxng:8080")
            results = await svc.search("test")
        assert len(results) == 1
        assert isinstance(results[0], SearchResult)
        assert results[0].title == "Test Article"
        assert results[0].snippet == "Some content here."
        assert results[0].url == "https://example.com"

    @pytest.mark.asyncio
    async def test_search_empty_results(self) -> None:
        fake = {"query": "x", "provider": "duckduckgo", "count": 0, "results": [], "formatted_text": ""}
        with patch("services.search_service.perform_web_search", new=AsyncMock(return_value=fake)):
            svc = SearchService()
            assert await svc.search("x") == []


class TestPerformWebSearchFormatting:
    @pytest.mark.asyncio
    async def test_no_results_message(self) -> None:
        with patch("services.search_service.search_duckduckgo", new=AsyncMock(return_value=[])):
            res = await perform_web_search("nichts", provider="duckduckgo")
        assert res["count"] == 0
        assert "Keine Online-Ergebnisse" in res["formatted_text"]

    @pytest.mark.asyncio
    async def test_formatted_text_lists_sources(self) -> None:
        hits = [{"title": "Quelle A", "snippet": "Snippet A", "url": "https://a.example"}]
        with patch("services.search_service.search_duckduckgo", new=AsyncMock(return_value=hits)):
            res = await perform_web_search("thema", provider="duckduckgo")
        assert res["count"] == 1
        assert "Quelle A" in res["formatted_text"]
        assert "https://a.example" in res["formatted_text"]

    @pytest.mark.asyncio
    async def test_searxng_falls_back_to_duckduckgo(self) -> None:
        hits = [{"title": "Fallback", "snippet": "S", "url": "https://f.example"}]
        with patch("services.search_service.search_searxng", new=AsyncMock(return_value=[])), \
             patch("services.search_service.search_duckduckgo", new=AsyncMock(return_value=hits)):
            res = await perform_web_search("thema", provider="searxng")
        assert res["count"] == 1
        assert res["results"][0]["title"] == "Fallback"
