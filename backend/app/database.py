from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings
from app.utils.migrations import run_startup_migrations

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
        event_market,
    )  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Apply lightweight, idempotent migrations for new columns.
        await run_startup_migrations(conn)


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
