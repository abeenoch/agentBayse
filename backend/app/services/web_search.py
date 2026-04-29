import httpx
from app.config import settings
from app.utils.logger import logger

TAVILY_ENDPOINT = "https://api.tavily.com/search"


class WebSearchService:
    def __init__(self):
        self.provider = settings.search_provider

    def _parse_domains(self, raw: str | None):
        if not raw:
            return None
        return [d.strip() for d in raw.split(",") if d.strip()]

    async def search(
        self,
        query: str,
        max_results: int | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        time_range: str | None = None,
        search_depth: str | None = None,
    ):
        if self.provider == "tavily" and settings.tavily_api_key:
            payload: dict = {
                "api_key": settings.tavily_api_key,
                "query": query,
                "max_results": max_results or settings.search_max_results,
                "search_depth": search_depth or settings.search_depth,
            }
            time_range = time_range or settings.search_time_range
            if time_range:
                payload["time_range"] = time_range
            include_domains = include_domains or self._parse_domains(settings.search_include_domains)
            exclude_domains = exclude_domains or self._parse_domains(settings.search_exclude_domains)
            if include_domains:
                payload["include_domains"] = include_domains
            if exclude_domains:
                payload["exclude_domains"] = exclude_domains
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(TAVILY_ENDPOINT, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    return {"provider": "tavily", "query": query, "results": data.get("results", [])}
            except httpx.HTTPStatusError as exc:
                logger.warning("Tavily HTTP error %s: %s", exc.response.status_code, exc.response.text)
                return {"provider": "tavily", "query": query, "results": []}
            except Exception as exc:
                logger.warning("Tavily request failed: %s", exc)
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
