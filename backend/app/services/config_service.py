from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.agent_config import AgentConfig
from app.config import settings


# Defaults seeded from env so first-run behavior mirrors operator intent.
DEFAULT_CONFIG = {
    "auto_trade": settings.agent_auto_trade,
    "categories": ["finance"],
    "max_trades_per_hour": 10,
    "max_trades_per_day": settings.agent_max_daily_trades,
    "max_open_positions": settings.agent_max_open_positions,
    "balance_floor": 0.0,
    "min_confidence": settings.agent_min_confidence,
    "balance_reserve_pct": settings.agent_balance_reserve_pct,
}


async def get_config(session: AsyncSession) -> AgentConfig:
    result = await session.execute(select(AgentConfig))
    cfg = result.scalars().first()
    if cfg:
        updated = False
        if getattr(cfg, "max_open_positions", 0) != settings.agent_max_open_positions:
            cfg.max_open_positions = settings.agent_max_open_positions
            updated = True
        if getattr(cfg, "min_confidence", settings.agent_min_confidence) > settings.agent_min_confidence:
            cfg.min_confidence = settings.agent_min_confidence
            updated = True
        if getattr(cfg, "balance_reserve_pct", None) is None:
            cfg.balance_reserve_pct = settings.agent_balance_reserve_pct
            updated = True
        if updated:
            await session.commit()
            await session.refresh(cfg)
        return cfg
    cfg = AgentConfig(**DEFAULT_CONFIG)
    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)
    return cfg


async def update_config(session: AsyncSession, payload: dict) -> AgentConfig:
    cfg = await get_config(session)
    for field, value in payload.items():
        if hasattr(cfg, field) and value is not None:
            setattr(cfg, field, value)
    await session.commit()
    await session.refresh(cfg)
    return cfg
