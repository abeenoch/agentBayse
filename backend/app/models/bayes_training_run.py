import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, Float, DateTime, JSON
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class BayesTrainingRun(Base):
    __tablename__ = "bayes_training_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    state_key = Column(String, nullable=False, index=True)
    model_version = Column(String, nullable=False, default="logreg_v1")
    sample_size = Column(Integer, nullable=False, default=0)
    train_size = Column(Integer, nullable=False, default=0)
    test_size = Column(Integer, nullable=False, default=0)
    positive_rate = Column(Float, nullable=False, default=0.0)
    feature_names = Column(JSON, nullable=False)
    coefficients = Column(JSON, nullable=False)
    metrics_json = Column(JSON, nullable=False)
    calibration_json = Column(JSON, nullable=False)
    trained_at = Column(DateTime, default=datetime.utcnow, nullable=False)
