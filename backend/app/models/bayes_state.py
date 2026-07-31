import uuid
from datetime import datetime

from sqlalchemy import Column, String, Float, DateTime, JSON, Integer
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class BayesState(Base):
    __tablename__ = "bayes_state"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_version = Column(String, nullable=False, default="v1", index=True)
    state_key = Column(String, nullable=False, unique=True, index=True)
    prior_json = Column(JSON, nullable=False)
    parameter_json = Column(JSON, nullable=False)
    calibration_json = Column(JSON, nullable=True)
    yes_updates = Column(Integer, nullable=False, default=0)
    no_updates = Column(Integer, nullable=False, default=0)
    last_posterior_yes = Column(Float, nullable=True)
    last_posterior_no = Column(Float, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
