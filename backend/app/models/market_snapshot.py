import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, DateTime, JSON
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id = Column(String, index=True, nullable=False)
    event_id = Column(String, index=True, nullable=False)
    title = Column(String, nullable=False)
    outcome1_label = Column(String, nullable=True)
    outcome1_price = Column(Float, nullable=True)
    outcome2_label = Column(String, nullable=True)
    outcome2_price = Column(Float, nullable=True)
    liquidity = Column(Float, nullable=True)
    total_volume = Column(Float, nullable=True)
    total_orders = Column(Integer, nullable=True)
    raw = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
