from math import sqrt
from typing import List


def calculate_total_pnl(positions: list) -> dict:
    total_cost = sum(p.get("cost", 0) for p in positions)
    total_value = sum(p.get("currentValue", 0) for p in positions)
    return {"total_cost": total_cost, "total_value": total_value, "pnl": total_value - total_cost}


def calculate_win_rate(resolved_trades: list) -> float:
    if not resolved_trades:
        return 0.0
    wins = sum(1 for t in resolved_trades if t.get("resolution") == "WIN")
    return wins / len(resolved_trades)


def calculate_sharpe_ratio(daily_returns: list[float]) -> float:
    if not daily_returns:
        return 0.0
    avg = sum(daily_returns) / len(daily_returns)
    variance = sum((r - avg) ** 2 for r in daily_returns) / len(daily_returns)
    std = sqrt(variance)
    if std == 0:
        return 0.0
    return avg / std


def calculate_max_drawdown(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for x in equity_curve:
        if x > peak:
            peak = x
        dd = (peak - x) / peak if peak else 0
        max_dd = max(max_dd, dd)
    return max_dd


def get_category_exposure(positions: list[dict]) -> dict[str, float]:
    exposure = {}
    for p in positions:
        cat = p.get("market", {}).get("event", {}).get("category", "unknown")
        exposure[cat] = exposure.get(cat, 0) + p.get("currentValue", 0)
    return exposure


def calculate_implied_probability(market_price: float) -> float:
    return market_price / 100


def calculate_expected_value(prob: float, price: float, stake: float) -> float:
    """
    Expected profit (same units as `stake`) for a binary market position.

    Args:
        prob: Probability the chosen outcome settles true (0-1).
        price: Current market price for that outcome. Supports 0-1 or 0-100 scales.
        stake: Currency amount we intend to allocate.

    For fractional prices (0-1) with 1.0 payout, EV = (prob - price) * (stake / price).
    For 0-100 prices (cents), price is converted to a 0-1 fraction first.
    """
    prob = min(max(prob, 0.0), 1.0)
    price_frac = price / 100 if price > 1 else price
    price_frac = min(max(price_frac, 1e-6), 0.999999)  # avoid divide-by-zero / absurd odds
    edge = prob - price_frac
    return edge * (stake / price_frac)


def calculate_kelly_criterion(prob: float, price: float) -> float:
    """
    Kelly fraction for a binary outcome with payout 1 and price in 0-1 or 0-100 scale.
    Returns fraction of bankroll to stake (0-1).
    """
    prob = min(max(prob, 0.0), 1.0)
    price_frac = price / 100 if price > 1 else price
    if price_frac <= 0 or price_frac >= 1:
        return 0.0
    b = (1 - price_frac) / price_frac
    q = 1 - prob
    edge = (b * prob - q) / b
    return max(0.0, edge)


def analyze_order_book_depth(order_book: dict) -> dict:
    bids = order_book.get("bids", [])
    asks = order_book.get("asks", [])
    bid_liquidity = sum(level.get("total", 0) for level in bids)
    ask_liquidity = sum(level.get("total", 0) for level in asks)
    return {"bid_liquidity": bid_liquidity, "ask_liquidity": ask_liquidity, "liquidity_score": min(bid_liquidity, ask_liquidity)}


def detect_price_momentum(price_history: list[float], window: int = 10) -> str:
    if len(price_history) < 2:
        return "NEUTRAL"
    recent = price_history[-window:]
    if recent[-1] > recent[0]:
        return "BULLISH"
    if recent[-1] < recent[0]:
        return "BEARISH"
    return "NEUTRAL"


def calculate_var(positions: list, confidence: float = 0.95) -> float:
    # Simple proxy: assume normal, use std dev of returns placeholder
    returns = [p.get("percentageChange", 0) / 100 for p in positions if p.get("percentageChange") is not None]
    if not returns:
        return 0.0
    avg = sum(returns) / len(returns)
    variance = sum((r - avg) ** 2 for r in returns) / len(returns)
    std = sqrt(variance)
    # Approx z for 95% one-tailed ~ -1.65; adjust if different confidence.
    z = -1.65
    return -(avg + z * std)


def check_concentration_risk(positions: list, max_single_exposure: float = 0.25) -> list[str]:
    total_value = sum(p.get("currentValue", 0) for p in positions) or 1
    warnings = []
    for p in positions:
        pct = p.get("currentValue", 0) / total_value
        if pct > max_single_exposure:
            warnings.append(f"{p.get('market', {}).get('title', 'unknown')} concentration {pct:.0%}")
    return warnings
