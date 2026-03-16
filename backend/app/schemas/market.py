from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class Outcome(BaseModel):
    id: str
    label: str
    price: float


class Market(BaseModel):
    id: str
    title: str
    status: str
    outcome1_label: Optional[str] = None
    outcome1_price: Optional[float] = None
    outcome2_label: Optional[str] = None
    outcome2_price: Optional[float] = None
    yes_buy_price: Optional[float] = None
    no_buy_price: Optional[float] = None
    fee_percentage: Optional[float] = None
    total_orders: Optional[int] = None
    rules: Optional[str] = None
    market_threshold: Optional[float] = None
    market_threshold_range: Optional[str] = None
    market_close_value: Optional[float] = None


class Event(BaseModel):
    id: str
    slug: str
    title: str
    description: Optional[str] = None
    category: Optional[str] = None
    type: Optional[str] = None
    engine: Optional[str] = None
    status: Optional[str] = None
    openingDate: Optional[datetime] = None
    resolutionDate: Optional[datetime] = None
    closingDate: Optional[datetime] = None
    imageUrl: Optional[str] = None
    liquidity: Optional[float] = None
    totalVolume: Optional[float] = None
    totalOrders: Optional[int] = None
    supportedCurrencies: Optional[list[str]] = None
    markets: Optional[List[Market]] = None
