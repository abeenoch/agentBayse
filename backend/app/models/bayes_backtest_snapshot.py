import uuid

from sqlalchemy import Column, String, Integer, DateTime, JSON, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class BayesBacktestSnapshot(Base):
    __tablename__ = "bayes_backtest_snapshots"
    __table_args__ = (
        UniqueConstraint("state_key", "period_kind", "period_key", name="uq_bayes_backtest_snapshot_scope"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    state_key = Column(String, nullable=False, index=True)
    period_kind = Column(String, nullable=False, index=True)  # all_time | daily
    period_key = Column(String, nullable=False, index=True)  # all_time or YYYY-MM-DD
    rows_scored = Column(Integer, nullable=False, default=0)
    generated_at = Column(DateTime, nullable=False)
    summary_json = Column(JSON, nullable=False)
