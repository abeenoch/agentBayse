from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.agent_config import AgentConfig


DEFAULT_CONFIG = {
    # Turn on autonomous trading by default (can be toggled off in Settings page)
    "auto_trade": True,
    "categories": [],
    "max_trades_per_hour": 10,
    "max_trades_per_day": 50,
    "balance_floor": 0.0,
}


async def get_config(session: AsyncSession) -> AgentConfig:
    result = await session.execute(select(AgentConfig))
    cfg = result.scalars().first()
    if cfg:
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
