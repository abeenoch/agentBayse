import uuid
from datetime import datetime

from sqlalchemy import Column, String, Float, DateTime, JSON, Integer
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class FeatureSnapshot(Base):
    __tablename__ = "feature_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id = Column(String, index=True, nullable=False)
    market_name = Column(String, nullable=False)
    event_id = Column(String, index=True, nullable=True)
    schema_version = Column(String, nullable=False, default="v1")
    feature_json = Column(JSON, nullable=False)
    semantic_vector = Column(JSON, nullable=False)
    market_vector = Column(JSON, nullable=False)
    portfolio_vector = Column(JSON, nullable=False)
    cross_market_vector = Column(JSON, nullable=False)
    posterior_yes = Column(Float, nullable=True)
    posterior_no = Column(Float, nullable=True)
    posterior_action = Column(String, nullable=True)
    posterior_confidence = Column(Float, nullable=True)
    model_version = Column(String, nullable=False, default="v1")
    source = Column(String, nullable=False, default="agent")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_signal_id = Column(UUID(as_uuid=True), nullable=True)
    resolved_trade_id = Column(UUID(as_uuid=True), nullable=True)
    vector_size_semantic = Column(Integer, nullable=False, default=8)
    vector_size_market = Column(Integer, nullable=False, default=8)
    vector_size_portfolio = Column(Integer, nullable=False, default=4)
    vector_size_cross_market = Column(Integer, nullable=False, default=4)
