
from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Optional

import httpx

from app.config import settings
from app.utils.logger import logger

_chroma_client = None
_collection = None
_embed_fn = None


def _get_collection():
    global _chroma_client, _collection, _embed_fn
    if _collection is not None:
        return _collection
    try:
        import chromadb
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

        _embed_fn = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
        _chroma_client = chromadb.PersistentClient(path="./chroma_db")
        _collection = _chroma_client.get_or_create_collection(
            name="market_knowledge",
            embedding_function=_embed_fn,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("ChromaDB collection ready (%d docs)", _collection.count())
    except ImportError:
        logger.warning("chromadb not installed — RAG disabled. Run: pip install chromadb sentence-transformers")
    return _collection


def _chunk(text: str, size: int = 400, overlap: int = 80) -> list[str]:
    """Split text into overlapping chunks of ~`size` words."""
    words = text.split()
    chunks, i = [], 0
    while i < len(words):
        chunks.append(" ".join(words[i : i + size]))
        i += size - overlap
    return [c for c in chunks if len(c.split()) > 20]


def _doc_id(url: str, chunk_idx: int) -> str:
    h = hashlib.md5(url.encode()).hexdigest()[:10]
    return f"{h}_{chunk_idx}"


def _clean_html(html: str) -> str:
    """Very lightweight HTML → plain text."""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()




async def _scrape_url(url: str, timeout: int = 8) -> str:
    """Fetch a URL and return plain text (best-effort)."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; BayseAgent/1.0)"},
            )
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            if "html" in ct:
                return _clean_html(resp.text)[:8000]
            return resp.text[:8000]
    except Exception as exc:
        logger.debug("Scrape failed for %s: %s", url, exc)
        return ""




async def ingest_market(topic: str, snippets: list[dict]) -> int:
    """
    Ingest search result snippets (and optionally scrape their URLs) into ChromaDB.

    `snippets` is the raw list from Tavily: [{"url": ..., "snippet": ..., "title": ...}, ...]
    Returns number of chunks added.
    """
    col = _get_collection()
    if col is None:
        return 0

    docs, ids, metas = [], [], []

    # Filter out forecast/prediction sites — they poison the RAG with irrelevant long-term data
    FORECAST_DOMAINS = {
        "coincodex.com", "walletinvestor.com", "digitalcoinprice.com",
        "cryptopredictions.com", "longforecast.com", "gov.capital",
        "priceprediction.net", "tradingbeasts.com", "previsioni-forex.com",
        "fxstreet.com/forecasts", "investing.com/analysis",
    }

    def _is_forecast_url(url: str) -> bool:
        url_lower = url.lower()
        return any(d in url_lower for d in FORECAST_DOMAINS) or "forecast" in url_lower or "prediction" in url_lower

    # Scrape URLs concurrently (cap at 5 to avoid hammering)
    urls = [r.get("url", "") for r in snippets if r.get("url") and not _is_forecast_url(r.get("url", ""))][:5]
    scraped = await asyncio.gather(*[_scrape_url(u) for u in urls], return_exceptions=True)

    for i, result in enumerate(snippets):
        url = result.get("url", "")
        # Skip forecast/prediction content
        if _is_forecast_url(url):
            continue
        base_text = result.get("snippet") or result.get("title") or ""

        # Add snippet as a chunk
        if base_text.strip():
            cid = _doc_id(url or topic, 0)
            docs.append(base_text)
            ids.append(cid)
            metas.append({"topic": topic, "url": url, "source": "snippet"})

        # Add scraped full text chunks
        if i < len(scraped) and isinstance(scraped[i], str) and scraped[i]:
            for ci, chunk in enumerate(_chunk(scraped[i]), start=1):
                cid = _doc_id(url, ci)
                docs.append(chunk)
                ids.append(cid)
                metas.append({"topic": topic, "url": url, "source": "scraped"})

    if not docs:
        return 0

    # Upsert (idempotent — same id = overwrite)
    try:
        col.upsert(documents=docs, ids=ids, metadatas=metas)
        logger.info("RAG: upserted %d chunks for topic '%s'", len(docs), topic[:60])
    except Exception as exc:
        logger.warning("RAG upsert failed for '%s': %s", topic, exc)
        return 0

    return len(docs)




def query(topic: str, k: int = 5) -> list[str]:
    """
    Return the top-k most relevant text chunks for `topic`.
    Returns empty list if ChromaDB is unavailable or collection is empty.
    """
    col = _get_collection()
    if col is None or col.count() == 0:
        return []
    try:
        results = col.query(query_texts=[topic], n_results=min(k, col.count()))
        return results.get("documents", [[]])[0]
    except Exception as exc:
        logger.warning("RAG query failed: %s", exc)
        return []
