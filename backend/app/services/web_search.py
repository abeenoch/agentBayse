import httpx
from app.config import settings

TAVILY_ENDPOINT = "https://api.tavily.com/search"


class WebSearchService:
    def __init__(self):
        self.provider = settings.search_provider

    async def search(self, query: str, max_results: int = 5):
        if self.provider == "tavily" and settings.tavily_api_key:
            try:
                async with httpx.AsyncClient(timeout=8) as client:
                    resp = await client.post(
                        TAVILY_ENDPOINT,
                        json={
                            "api_key": settings.tavily_api_key,
                            "query": query,
                            "max_results": max_results,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    return {"provider": "tavily", "query": query, "results": data.get("results", [])}
            except Exception:
                return {"provider": "tavily", "query": query, "results": []}
        # fallback mock
        return {
            "provider": self.provider,
            "query": query,
            "results": [
                {"title": "Placeholder result", "url": "https://example.com", "snippet": "Replace with real search."}
            ],
        }


def get_search_service() -> WebSearchService:
    return WebSearchService()
