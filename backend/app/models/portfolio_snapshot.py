import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, DateTime, JSON
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    total_balance = Column(Float, nullable=True)
    invested = Column(Float, nullable=True)
    unrealized_pnl = Column(Float, nullable=True)
    realized_pnl_today = Column(Float, nullable=True)
    positions_count = Column(Integer, nullable=True)
    snapshot_data = Column(JSON, nullable=True)
