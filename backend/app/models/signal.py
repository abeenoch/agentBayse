import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, DateTime, JSON
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class Signal(Base):
    __tablename__ = "signals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id = Column(String, nullable=False)
    market_name = Column(String, nullable=False)
    signal_type = Column(String, nullable=False)
    confidence = Column(Integer, nullable=False)
    estimated_probability = Column(Float, nullable=False)
    market_price_at_signal = Column(Float, nullable=False)
    expected_value = Column(Float, nullable=False)
    reasoning = Column(String, nullable=False)
    sources = Column(JSON, nullable=True)
    suggested_stake = Column(Float, nullable=False)
    risk_level = Column(String, nullable=False)
    status = Column(String, nullable=False, default="PENDING")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    approved_at = Column(DateTime, nullable=True)
    agent_cycle_id = Column(String, nullable=True)
    executed_order_id = Column(String, nullable=True)
    executed_at = Column(DateTime, nullable=True)
