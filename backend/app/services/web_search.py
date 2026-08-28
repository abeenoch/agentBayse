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

    async def _search_duckduckgo(self, query: str, max_results: int = 5) -> dict:
        """Search via DuckDuckGo — free, no API key, no quota."""
        try:
            # Prefer ddgs (v9+) over the deprecated duckduckgo_search
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS  # type: ignore[no-redef]

            def _run():
                results = []
                with DDGS() as ddgs:
                    for r in ddgs.text(query, max_results=max_results):
                        results.append({
                            "title": r.get("title", ""),
                            "url": r.get("href", ""),
                            "snippet": r.get("body", ""),
                        })
                return results

            import asyncio
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(None, _run)
            logger.info("DuckDuckGo returned %d results for '%s'", len(results), query[:60])
            return {"provider": "duckduckgo", "query": query, "results": results}
        except ImportError:
            logger.warning("ddgs/duckduckgo_search not installed — run: pip install ddgs")
        except Exception as exc:
            logger.warning("DuckDuckGo search failed for '%s': %s", query[:60], exc)
        return {"provider": "duckduckgo", "query": query, "results": []}

    async def search(
        self,
        query: str,
        max_results: int | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        time_range: str | None = None,
        search_depth: str | None = None,
    ):
        max_results = max_results or settings.search_max_results

        if self.provider == "tavily" and settings.tavily_api_key:
            payload: dict = {
                "api_key": settings.tavily_api_key,
                "query": query,
                "max_results": max_results,
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
                logger.warning(
                    "Tavily HTTP error %s: %s — falling back to DuckDuckGo",
                    exc.response.status_code,
                    exc.response.text[:120],
                )
                return await self._search_duckduckgo(query, max_results)
            except Exception as exc:
                logger.warning("Tavily request failed: %s — falling back to DuckDuckGo", exc)
                return await self._search_duckduckgo(query, max_results)

        if self.provider == "duckduckgo":
            return await self._search_duckduckgo(query, max_results)

        # Last resort: mock placeholder
        logger.warning("No search provider configured — returning placeholder results")
        return {
            "provider": self.provider,
            "query": query,
            "results": [
                {"title": "Placeholder result", "url": "https://example.com", "snippet": "Replace with real search."}
            ],
        }


def get_search_service() -> WebSearchService:
    return WebSearchService()
