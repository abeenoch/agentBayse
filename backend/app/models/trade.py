import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, DateTime
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class Trade(Base):
    __tablename__ = "trades"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bayse_order_id = Column(String, unique=True, nullable=True)
    market_id = Column(String, nullable=False)
    market_name = Column(String, nullable=False)
    side = Column(String, nullable=False)
    shares = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    total_cost = Column(Float, nullable=False)
    status = Column(String, nullable=False, default="PENDING_APPROVAL")
    source = Column(String, nullable=False, default="MANUAL")
    signal_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    executed_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution = Column(String, nullable=True)
    pnl = Column(Float, nullable=True)
