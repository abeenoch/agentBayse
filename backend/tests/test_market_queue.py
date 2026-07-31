from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services import scheduler as scheduler_module


class FakeBayseClient:
    def __init__(self):
        self.calls: list[str] = []

    async def list_events(self, category=None, status="open", keyword=None, page=1, size=50, trending=None, series_slug=None):
        self.calls.append(series_slug)
        if series_slug == "fx-gbpusd-1h":
            return {
                "events": [
                    {
                        "id": "event-fx",
                        "seriesSlug": "fx-gbpusd-1h",
                        "category": "finance",
                        "markets": [{"id": "m-fx", "title": "FX market"}],
                    }
                ]
            }
        if series_slug == "sports-1h":
            return {
                "events": [
                    {
                        "id": "event-sports",
                        "seriesSlug": "sports-1h",
                        "category": "sports",
                        "markets": [{"id": "m-sports", "title": "Sports market"}],
                    }
                ]
            }
        return {"events": []}


@pytest.mark.asyncio
async def test_populate_queue_uses_series_slugs_and_category_filter(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    client = FakeBayseClient()

    async def fake_get_config(session):
        return SimpleNamespace(categories=["finance"])

    monkeypatch.setattr(scheduler_module, "AsyncSessionLocal", AsyncLocal)
    monkeypatch.setattr(scheduler_module, "get_bayse_client", lambda: client)
    monkeypatch.setattr(scheduler_module, "get_config", fake_get_config)
    monkeypatch.setattr(scheduler_module.settings, "agent_series_slugs", "fx-gbpusd-1h,sports-1h")

    scheduler_module.pending_markets.clear()
    await scheduler_module.populate_queue()

    assert client.calls == ["fx-gbpusd-1h", "sports-1h"]
    assert len(scheduler_module.pending_markets) == 1
    assert scheduler_module.pending_markets[0]["event"]["id"] == "event-fx"
    assert scheduler_module.pending_markets[0]["market"]["id"] == "m-fx"

