import pytest
import httpx

from app.services.web_search import WebSearchService


@pytest.mark.asyncio
async def test_tavily_payload_accepts_overrides(monkeypatch):
    from app.config import settings

    # ensure tavily path is used
    monkeypatch.setattr(settings, "tavily_api_key", "test-key")
    monkeypatch.setattr(settings, "search_provider", "tavily")
    monkeypatch.setattr(settings, "search_max_results", 8)
    monkeypatch.setattr(settings, "search_depth", "advanced")

    captured = {}

    class FakeResponse:
        status_code = 200

        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

        def raise_for_status(self):
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse({"results": [{"url": "https://example.com"}]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=10: FakeClient())

    service = WebSearchService()
    result = await service.search(
        "test query",
        max_results=3,
        include_domains=["a.com"],
        exclude_domains=["b.com"],
        time_range="week",
        search_depth="fast",
    )

    assert captured["url"].endswith("/search")
    assert captured["json"]["query"] == "test query"
    assert captured["json"]["max_results"] == 3
    assert captured["json"]["search_depth"] == "fast"
    assert captured["json"]["time_range"] == "week"
    assert captured["json"]["include_domains"] == ["a.com"]
    assert captured["json"]["exclude_domains"] == ["b.com"]
    assert result["results"][0]["url"] == "https://example.com"
