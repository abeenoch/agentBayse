import uuid
from sqlalchemy import Column, String, Integer, Float, Boolean, JSON
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class AgentConfig(Base):
    __tablename__ = "agent_config"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    auto_trade = Column(Boolean, default=False, nullable=False)
    categories = Column(JSON, nullable=True)  # list of categories to scan; None/empty => all
    max_trades_per_hour = Column(Integer, default=10, nullable=False)
    max_trades_per_day = Column(Integer, default=50, nullable=False)
    max_open_positions = Column(Integer, default=3, nullable=False)
    balance_floor = Column(Float, default=0.0, nullable=False)
    min_confidence = Column(Integer, default=50, nullable=False)
    balance_reserve_pct = Column(Float, default=0.30, nullable=False)  # keep 30% of balance untouched
