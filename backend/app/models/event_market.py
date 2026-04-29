from datetime import datetime
from sqlalchemy import Column, String, DateTime

from app.database import Base


class EventMarket(Base):
    __tablename__ = "event_markets"

    event_id = Column(String, primary_key=True, index=True)
    market_id = Column(String, primary_key=True, index=True)
    status = Column(String, nullable=False, default="PENDING")
    last_analyzed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
