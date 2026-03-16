from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class SignalBase(BaseModel):
    market_id: str
    market_name: str
    signal_type: str
    confidence: int
    estimated_probability: float
    market_price_at_signal: float
    expected_value: float
    reasoning: str
    sources: Optional[list[str]] = None
    suggested_stake: float
    risk_level: str
    status: str = "PENDING"


class SignalCreate(BaseModel):
    market_id: str
    market_name: str
    signal_type: str
    confidence: int
    estimated_probability: float
    market_price_at_signal: float
    expected_value: float
    reasoning: str
    sources: Optional[List[str]] = None
    suggested_stake: float
    risk_level: str


class SignalRead(SignalBase):
    id: str
    created_at: datetime
    approved_at: Optional[datetime] = None
    agent_cycle_id: Optional[str] = None

    class Config:
        from_attributes = True
