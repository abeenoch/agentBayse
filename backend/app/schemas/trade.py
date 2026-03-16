from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class TradeBase(BaseModel):
    market_id: str
    market_name: str
    side: str
    shares: int
    price: float
    total_cost: float
    status: str
    source: str


class TradeCreate(BaseModel):
    market_id: str
    side: str
    shares: int
    price: float
    currency: str | None = "NGN"


class TradeRead(TradeBase):
    id: str
    bayse_order_id: Optional[str] = None
    signal_id: Optional[str] = None
    created_at: datetime
    executed_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    resolution: Optional[str] = None
    pnl: Optional[float] = None

    class Config:
        from_attributes = True
