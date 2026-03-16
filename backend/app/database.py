from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import text

from app.config import settings

engine = create_async_engine(settings.database_url, future=True, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


async def init_db():
    # Import models to register metadata
    from app.models import (
        trade,
        signal,
        market_snapshot,
        portfolio_snapshot,
        analysis_state,
        agent_config,
    )  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight, backwards‑compatible migrations for SQLite dev DBs
        # (create_all won't add columns to existing tables).
        if settings.database_url.startswith("sqlite"):
            from sqlalchemy import text

            # Add missing columns on signals table so old local DBs stop crashing
            res = await conn.execute(text("PRAGMA table_info('signals');"))
            cols = {row[1] for row in res}
            if "executed_order_id" not in cols:
                await conn.execute(text("ALTER TABLE signals ADD COLUMN executed_order_id VARCHAR;"))
            if "executed_at" not in cols:
                await conn.execute(text("ALTER TABLE signals ADD COLUMN executed_at DATETIME;"))


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
