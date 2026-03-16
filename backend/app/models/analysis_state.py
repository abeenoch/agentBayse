from datetime import datetime
from sqlalchemy import Column, String, DateTime

from app.database import Base


class AnalysisState(Base):
    __tablename__ = "analysis_state"

    market_id = Column(String, primary_key=True, index=True)
    last_analyzed = Column(DateTime, default=datetime.utcnow, nullable=False)
