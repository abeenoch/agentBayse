from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.analysis_state import AnalysisState
from app.models.event_market import EventMarket
from app.services.analysis import calculate_expected_value
from app.services.bayse_client import BayseClient, get_bayse_client
from app.services.config_service import get_config
from app.services.llm_client import call_llm, provider_name
from app.services.risk_guard import check_trade_limits, risk_guard
from app.services.storage import save_signal
from app.services.web_search import WebSearchService, get_search_service
from app.utils.logger import logger
from app.websocket_manager import manager
from app.services.trade_executor import execute_signal
from app.services.bayse_client import BayseAuthError


class SignalOutput(BaseModel):
    """Structured trading signal returned by the LLM."""

    market_id: str
    market_name: str
    signal: str = Field(description="One of: BUY_YES | BUY_NO | SELL | HOLD | AVOID")
    confidence: int = Field(ge=0, le=100)
    estimated_probability: float = Field(ge=0.0, le=1.0)
    current_market_price: float = Field(ge=0.0)
    expected_value: float
    reasoning: str = Field(max_length=400)
    sources: list[str] = Field(default_factory=list)
    suggested_stake: float = Field(ge=0.0)
    risk_level: str = Field(description="One of: LOW | MEDIUM | HIGH")
    # Sniper timing — only populated during snipe analysis
    entry_timing: str = Field(default="ENTER_NOW", description="One of: ENTER_NOW | WAIT | SKIP")
    entry_delay_seconds: int = Field(default=0, ge=0, description="Seconds to wait before re-evaluating (used when entry_timing=WAIT)")

    @field_validator("signal")
    @classmethod
    def validate_signal(cls, v: str) -> str:
        allowed = {"BUY_YES", "BUY_NO", "SELL", "HOLD", "AVOID"}
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"signal must be one of {allowed}, got '{v}'")
        return v

    @field_validator("risk_level")
    @classmethod
    def validate_risk_level(cls, v: str) -> str:
        allowed = {"LOW", "MEDIUM", "HIGH"}
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"risk_level must be one of {allowed}, got '{v}'")
        return v

    @field_validator("entry_timing")
    @classmethod
    def validate_entry_timing(cls, v: str) -> str:
        allowed = {"ENTER_NOW", "WAIT", "SKIP"}
        v = v.upper()
        return v if v in allowed else "ENTER_NOW"


SYSTEM_PROMPT = (
    "You are an expert Bayse Markets prediction-market trader managing real money.\n"
    "Analyse the provided market data, portfolio state, and news, then return a "
    "single trading signal as a JSON object with EXACTLY these fields:\n"
    "  market_id, market_name, signal (BUY_YES|BUY_NO|SELL|HOLD|AVOID), "
    "confidence (0-100), estimated_probability (0.0-1.0), "
    "current_market_price (float), expected_value (float), "
    "reasoning (<=400 chars), sources (list of urls), "
    "suggested_stake (float), risk_level (LOW|MEDIUM|HIGH).\n"
    "\n"
    "Decision process — follow this order:\n"
    "  1. Read the portfolio state: how much is deployed, how many open bets, available balance.\n"
    "  2. Read the timeframe — this is CRITICAL:\n"
    "     - Short (<2h): ONLY use today's price action, intraday momentum, and breaking news.\n"
    "       IGNORE all weekly/monthly/yearly forecasts — they are irrelevant for a 1-hour market.\n"
    "     - Long (>2h): use macro trends and news.\n"
    "  3. Read recent win/loss record. If on a losing streak, be more conservative.\n"
    "  4. Argue the NO case first — why might the NO outcome win? Then argue YES.\n"
    "  5. Only after arguing both sides, assign estimated_probability.\n"
    "  6. BUY_YES: only when estimated_probability > yes_price AND EV > 0 AND confidence >= 60.\n"
    "  7. BUY_NO: only when estimated_probability < no_price AND EV > 0 AND confidence >= 60.\n"
    "  8. HOLD or AVOID: when edge is unclear, confidence < 60, or portfolio is already stretched.\n"
    "\n"
    "CRITICAL rules:\n"
    "  - Do NOT use December 2026 or year-end forecasts to decide a market closing TODAY.\n"
    "  - Do NOT default to BUY_YES. At 50/50 prices, NO is equally valid — argue it properly.\n"
    "  - suggested_stake must respect the available_to_deploy shown in portfolio context.\n"
    "  - If open_positions >= 2, only bet if confidence >= 75 (high conviction only).\n"
    "  - If open_positions >= 3, return HOLD regardless — portfolio is at capacity.\n"
    "  - Base probability on TODAY's price action and news, not long-term forecasts.\n"
    "\n"
    "expected_value formula: (prob * (100 - price) - (1 - prob) * price) * stake / 100\n"
    "Respond with ONLY the JSON object, no markdown, no extra text."
)

SNIPE_SYSTEM_PROMPT = (
    "You are an expert short-interval prediction-market trader specialising in "
    "crypto and FX markets that close in minutes. "
    "You are given live market data including current price, recent price momentum, "
    "time remaining, and news context. "
    "Return a JSON object with EXACTLY these fields:\n"
    "  market_id, market_name, signal (BUY_YES|BUY_NO|HOLD|AVOID), "
    "confidence (0-100), estimated_probability (0.0-1.0), "
    "current_market_price (float), expected_value (float), "
    "reasoning (<=400 chars), sources (list of urls), "
    "suggested_stake (float), risk_level (LOW|MEDIUM|HIGH), "
    "entry_timing (ENTER_NOW|WAIT|SKIP), "
    "entry_delay_seconds (integer — seconds to wait before re-evaluating, 0 if ENTER_NOW or SKIP).\n"
    "entry_timing rules:\n"
    "  ENTER_NOW — conditions are good, place the bet immediately.\n"
    "  WAIT — price is still moving, wait entry_delay_seconds then re-evaluate.\n"
    "  SKIP — no edge, do not bet.\n"
    "Respond with ONLY the JSON object, no markdown, no extra text."
)


async def _get_market_history(session: AsyncSession, market_id: str, limit: int = 5) -> list[dict]:
    """Fetch recent signals for this market so the LLM has prior context."""
    from sqlalchemy import select as sa_select
    from app.models.signal import Signal as SignalModel
    result = await session.execute(
        sa_select(SignalModel)
        .where(SignalModel.market_id == market_id)
        .order_by(SignalModel.created_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    history = []
    for r in reversed(rows):  # chronological order
        entry = {
            "signal": r.signal_type,
            "confidence": r.confidence,
            "prob": r.estimated_probability,
            "price": r.market_price_at_signal,
            "ev": r.expected_value,
            "reasoning": (r.reasoning or "")[:120],
        }
        if r.resolution:
            entry["outcome"] = r.resolution
        if r.pnl is not None:
            entry["pnl"] = round(r.pnl, 2)
        history.append(entry)
    return history


def _build_user_prompt(
    market_id: str,
    market_name: str,
    yes_price: float,
    no_price: float,
    description: str,
    snippets: list[str],
    sources: list[str],
    rag_chunks: list[str] | None = None,
    history: list[dict] | None = None,
    portfolio_ctx: dict | None = None,
    time_remaining: str = "unknown",
    timeframe: str = "unknown",
) -> str:
    lines = []

    # --- Portfolio state ---
    if portfolio_ctx:
        p = portfolio_ctx
        lines += [
            "=== PORTFOLIO STATE ===",
            f"Total balance:       {p.get('balance', 'N/A')}",
            f"Reserve (30% kept):  {p.get('reserve', 'N/A')}",
            f"Available to deploy: {p.get('available_to_deploy', 'N/A')}",
            f"Already deployed:    {p.get('deployed', 'N/A')}",
            f"Open positions:      {p.get('open_positions', 0)}",
            f"Today's bets:        {p.get('bets_today', 0)}",
            f"Recent record:       {p.get('recent_record', 'N/A')} (last 10 resolved)",
            "======================",
            "",
        ]

    # --- Market data ---
    lines += [
        f"Market: {market_name}",
        f"Market ID: {market_id}",
        f"Description: {description or 'N/A'}",
        f"Time remaining: {time_remaining}",
        f"Timeframe strategy: {timeframe}",
        f"Current YES price: {yes_price:.4f}  →  buy YES only if your prob estimate > {yes_price:.4f}",
        f"Current NO price:  {no_price:.4f}  →  buy NO  only if your prob estimate < {no_price:.4f}",
    ]

    if abs(yes_price - no_price) < 0.02:
        lines.append(
            "NOTE: 50/50 market — no price signal. Argue the NO case first, then YES. "
            "Do not default to YES. BUY_NO is equally valid."
        )

    # --- Prior signals ---
    if history:
        lines.append("\nPrior signals on this market (oldest → newest):")
        for h in history:
            outcome_str = f" → {h['outcome']} (pnl={h.get('pnl', '?')})" if "outcome" in h else " → unresolved"
            lines.append(
                f"  [{h['signal']} conf={h['confidence']} prob={h['prob']:.2f} "
                f"@ {h['price']:.3f}]{outcome_str} | {h['reasoning']}"
            )
        lines.append("Avoid repeating losing patterns on this market.")

    # --- RAG background ---
    if rag_chunks:
        lines.append("\nBackground knowledge (knowledge base):")
        lines.extend(f"  [{i+1}] {c}" for i, c in enumerate(rag_chunks))
        # Warn if timeframe is short — long-term forecasts in RAG are misleading
        if "short" in timeframe or "medium" in timeframe:
            lines.append(
                "  WARNING: Ignore any weekly/monthly/yearly price forecasts above — "
                "they are irrelevant for a market closing within hours. "
                "Focus only on today's intraday price action."
            )

    # --- Live news ---
    if snippets:
        lines.append("\nLive news:")
        lines.extend(f"  - {s}" for s in snippets)
    if sources:
        lines.append(f"  Sources: {sources}")

    lines.append("\nNow argue the NO case, then the YES case, then return the JSON signal.")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    """Extract the first JSON object from an LLM response."""
    # Strip markdown code fences if present
    text = re.sub(r"```(?:json)?", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in LLM response: {text[:200]}")
    return json.loads(match.group())


@dataclass
class AgentSignal:
    market_id: str
    market_name: str
    signal: str
    confidence: int
    estimated_probability: float
    current_market_price: float
    expected_value: float
    rank_score: float
    reasoning: str
    sources: list[str]
    suggested_stake: float
    risk_level: str
    event_id: str = ""


class AIAgent:
    def __init__(
        self,
        search_service: Optional[WebSearchService] = None,
        bayse_client: Optional[BayseClient] = None,
    ):
        self.search = search_service or get_search_service()
        self.bayse = bayse_client or get_bayse_client()
        self.last_analyzed: dict[str, datetime] = {}

    async def analyze_market(
        self,
        market_id: str,
        event: Optional[dict] = None,
        session: Optional[AsyncSession] = None,
    ) -> Optional[AgentSignal]:

        cfg = None
        event_id: str = ""

        if session is not None:
            cfg = await get_config(session)

        now = datetime.utcnow()
        last = self.last_analyzed.get(market_id)
        if last and now - last < timedelta(minutes=settings.agent_reanalyze_minutes):
            logger.info("Skipping %s (recent analysis window)", market_id)
            return None

        if session is not None:
            state = await session.get(AnalysisState, market_id)
            if state and now - state.last_analyzed < timedelta(minutes=settings.agent_reanalyze_minutes):
                logger.info("Skipping %s (recent analysis in DB window)", market_id)
                return None

        if event and isinstance(event, dict):
            event_id = event.get("id") or event.get("eventId") or ""

        if session is not None and event_id:
            em = await session.get(EventMarket, {"event_id": event_id, "market_id": market_id})
            if em and em.status == "COMPLETED":
                return None

        event_data = event or await self.bayse.get_event(market_id)
        if not event_data:
            return None

        if not event_id and isinstance(event_data, dict):
            event_id = event_data.get("id") or event_data.get("eventId") or ""

        market: dict = {}
        if isinstance(event_data, dict) and event_data.get("markets"):
            for m in event_data["markets"]:
                if m["id"] == market_id:
                    market = m
                    break
            if not market:
                market = event_data["markets"][0]
        else:
            market = event_data  # type: ignore[assignment]

        title: str = (
            # Prefer event title — market title is often just "Yes" (the outcome label)
            (event_data.get("title") if isinstance(event_data, dict) else "")
            or market.get("title")
            or market_id
        )
        # If title still looks like an outcome label, fall back to market description or id
        if title.strip().lower() in ("yes", "no", "yes or no"):
            title = description[:80] or market_id
        yes_price = float(market.get("outcome1Price") or 0.5)
        no_price = float(market.get("outcome2Price") or (1 - yes_price))
        description: str = (event_data.get("description") if isinstance(event_data, dict) else "") or ""

        # Extract closing time and compute time remaining
        from datetime import timezone
        closing_raw = (
            event_data.get("closingDate") or event_data.get("resolutionDate")
            or market.get("closingDate") or market.get("resolutionDate")
        ) if isinstance(event_data, dict) else None
        closing_dt = None
        time_remaining_str = "unknown"
        timeframe_str = "unknown"
        if closing_raw:
            try:
                closing_dt = datetime.fromisoformat(str(closing_raw).replace("Z", "+00:00"))
                if closing_dt.tzinfo is None:
                    closing_dt = closing_dt.replace(tzinfo=timezone.utc)
                secs_left = (closing_dt - datetime.now(tz=timezone.utc)).total_seconds()
                if secs_left > 3600:
                    time_remaining_str = f"{secs_left/3600:.1f} hours"
                    timeframe_str = "long (>1h) — use macro/news analysis"
                elif secs_left > 300:
                    time_remaining_str = f"{secs_left/60:.0f} minutes"
                    timeframe_str = "medium (5-60min) — use recent momentum + news"
                else:
                    time_remaining_str = f"{secs_left:.0f} seconds"
                    timeframe_str = "short (<5min) — use price momentum only"
            except Exception:
                pass

        market_heat = self._market_hotness(market)
        logger.info(
            "Analyzing market %s | title='%s' yes=%.3f no=%.3f desc_snippet='%s'",
            market_id,
            title,
            yes_price,
            no_price,
            (description or "")[:120],
        )

        # Fetch search snippets — use time-aware query for short-term markets
        if closing_dt:
            from datetime import timezone as _tz
            secs_left = (closing_dt - datetime.now(tz=_tz.utc)).total_seconds()
            if secs_left < 7200:  # < 2 hours
                search_query = f"{title} price now today intraday"
            else:
                search_query = f"{title} latest news today"
        else:
            search_query = f"{title} latest news"

        search_resp = await self.search.search(search_query, max_results=5)
        raw = search_resp.get("results", [])
        fallback_sources = [r.get("url", "") for r in raw if r.get("url")][:5]
        fallback_snippets = [r.get("snippet") or r.get("title") or "" for r in raw][:5]

        # Ingest into RAG (non-blocking — fire and forget)
        from app.services import rag as rag_service
        asyncio.create_task(rag_service.ingest_market(title, raw))

        # Retrieve relevant context from RAG knowledge base
        rag_chunks = rag_service.query(title, k=5)

        # Fetch prior signal history for this market
        history: list[dict] = []
        if session is not None:
            history = await _get_market_history(session, market_id, limit=5)

        # Build portfolio context for the LLM
        portfolio = await self.bayse.get_portfolio() or {}
        # Use real wallet balance (cash available to spend), not portfolio market value
        wallet_balance = await self.bayse.get_wallet_balance()
        logger.info("Wallet balance fetch: ₦%.2f (currency=%s)", wallet_balance, self.bayse.default_currency)
        available_balance = wallet_balance if wallet_balance > 0 else float(
            portfolio.get("portfolioCurrentValue")
            or portfolio.get("availableBalance")
            or portfolio.get("walletBalance")
            or portfolio.get("balance")
            or 0.0
        )
        deployed = float(portfolio.get("portfolioCost") or 0.0)
        reserve_pct = getattr(cfg, "balance_reserve_pct", settings.agent_balance_reserve_pct) if cfg else settings.agent_balance_reserve_pct
        reserve = available_balance * reserve_pct

        if available_balance > 0:
            available_to_deploy = max(available_balance * (1 - reserve_pct) - deployed, 0.0)
        else:
            # Balance unavailable — use max_position_size as a safe fallback budget
            available_to_deploy = settings.agent_max_position_size

        # Open positions count
        open_positions = 0
        bets_today = 0
        recent_record = "N/A"
        if session is not None:
            from sqlalchemy import select as sa_select, func as sa_func
            from app.models.trade import Trade as TradeModel
            from app.models.signal import Signal as SignalModel
            from datetime import date

            op_q = await session.execute(
                sa_select(sa_func.count()).select_from(TradeModel).where(
                    TradeModel.status == "EXECUTED", TradeModel.resolution.is_(None)
                )
            )
            open_positions = op_q.scalar_one()

            today = date.today()
            bt_q = await session.execute(
                sa_select(sa_func.count()).select_from(SignalModel).where(
                    SignalModel.executed_at.isnot(None),
                    sa_func.date(SignalModel.executed_at) == today,
                )
            )
            bets_today = bt_q.scalar_one()

            # Recent win/loss record (last 10 resolved)
            rec_q = await session.execute(
                sa_select(SignalModel.resolution)
                .where(SignalModel.resolution.isnot(None))
                .order_by(SignalModel.created_at.desc())
                .limit(10)
            )
            resolutions = [r[0] for r in rec_q.all()]
            if resolutions:
                wins = sum(1 for r in resolutions if r == "WIN")
                recent_record = f"{wins}W/{len(resolutions)-wins}L"

        portfolio_ctx = {
            "balance": f"₦{available_balance:,.0f}",
            "reserve": f"₦{reserve:,.0f} ({reserve_pct*100:.0f}%)",
            "available_to_deploy": f"₦{available_to_deploy:,.0f}",
            "deployed": f"₦{deployed:,.0f}",
            "open_positions": open_positions,
            "bets_today": bets_today,
            "recent_record": recent_record,
        }

        user_prompt = _build_user_prompt(
            market_id=market_id,
            market_name=title,
            yes_price=yes_price,
            no_price=no_price,
            description=description,
            snippets=fallback_snippets,
            sources=fallback_sources,
            rag_chunks=rag_chunks,
            history=history,
            portfolio_ctx=portfolio_ctx,
            time_remaining=time_remaining_str,
            timeframe=timeframe_str,
        )

        output: Optional[SignalOutput] = None
        try:
            raw_text = await call_llm(user_prompt, system=SYSTEM_PROMPT)
            # Log the full raw LLM response so we can verify real analysis is happening
            logger.info(
                "LLM raw response for %s:\n%s",
                market_id,
                raw_text[:1000],
            )
            data = _extract_json(raw_text)
            data["market_id"] = market_id
            data["market_name"] = title
            if "reasoning" in data and len(str(data["reasoning"])) > 397:
                data["reasoning"] = str(data["reasoning"])[:397] + "..."
            output = SignalOutput(**data)
            logger.info(
                "LLM decision: market=%s signal=%s prob=%.2f conf=%d ev=%.2f reasoning='%s'",
                title[:60],
                output.signal,
                output.estimated_probability,
                output.confidence,
                output.expected_value,
                (output.reasoning or "")[:150],
            )
        except Exception as exc:
            logger.warning("LLM call failed for market %s: %s", market_id, exc, exc_info=True)
            return None

        # Normalize probability for the chosen direction and recompute stake/EV using live prices.
        direction_prob = self._direction_probability(output.signal, output.estimated_probability)
        ref_price = self._pick_price_for_signal(output.signal, yes_price, no_price)
        if direction_prob <= ref_price:
            direction_prob = min(ref_price + 0.05, 0.95)
        output.confidence = max(output.confidence or 0, int(50 + (direction_prob - ref_price) * 100))

        stake = self._normalized_stake(
            output.suggested_stake,
            available_balance,
            available_to_deploy,
            open_positions,
            output.confidence,
            cfg,
        )
        output.suggested_stake = stake
        output.current_market_price = ref_price
        output.estimated_probability = direction_prob
        output.expected_value = calculate_expected_value(
            prob=direction_prob,
            price=ref_price,
            stake=stake,
        )

        rank_score = self._rank_signal(output, market_heat)
        sources = output.sources if output.sources else fallback_sources

        signal = AgentSignal(
            market_id=output.market_id,
            market_name=output.market_name,
            signal=output.signal,
            confidence=output.confidence,
            estimated_probability=output.estimated_probability,
            current_market_price=output.current_market_price,
            expected_value=output.expected_value,
            rank_score=rank_score,
            reasoning=output.reasoning,
            sources=sources,
            suggested_stake=output.suggested_stake,
            risk_level=output.risk_level,
            event_id=event_id,
        )

        rg = risk_guard(signal.__dict__, {**portfolio, "_wallet_balance": available_balance}, cfg)
        if not rg.passed:
            logger.info(
                "Risk guard blocked signal %s (%s) ev=%.3f conf=%s reasons=%s",
                signal.market_id,
                signal.market_name,
                signal.expected_value,
                signal.confidence,
                rg.reasons,
            )
            return None

        if cfg and cfg.balance_floor and portfolio:
            bal = portfolio.get("portfolioCurrentValue") or portfolio.get("availableBalance")
            if bal is not None and bal <= cfg.balance_floor:
                logger.info("Balance floor reached (%.2f <= %.2f); skipping", bal, cfg.balance_floor)
                return None

        if session is not None and cfg is not None:
            tl = await check_trade_limits(session, cfg, portfolio=portfolio)
            if not tl.passed:
                logger.info(
                    "Trade limits blocked signal %s (%s) reasons=%s",
                    signal.market_id,
                    signal.market_name,
                    tl.reasons,
                )
                return None

        saved_signal = None
        if session is not None:
            # Only persist actionable signals — HOLD/AVOID are noise in the DB
            if signal.signal in ("BUY_YES", "BUY_NO"):
                saved_signal = await save_signal(session, signal.__dict__)

            db_state = (
                await session.get(AnalysisState, market_id)
                or AnalysisState(market_id=market_id)
            )
            db_state.last_analyzed = datetime.utcnow()
            session.add(db_state)

            if event_id:
                em = (
                    await session.get(EventMarket, {"event_id": event_id, "market_id": market_id})
                    or EventMarket(event_id=event_id, market_id=market_id)
                )
                em.status = "COMPLETED"
                em.last_analyzed_at = datetime.utcnow()
                session.add(em)

            await session.commit()

            if cfg and getattr(cfg, "auto_trade", False) and saved_signal is not None:
                try:
                    await execute_signal(session, self.bayse, saved_signal, event_data=event_data)
                    logger.info("Auto-traded signal %s (%s)", saved_signal.id, saved_signal.market_name)
                except BayseAuthError as exc:
                    logger.error("Auto-trade auth failed for %s: %s", market_id, exc)
                except Exception as exc:
                    logger.error("Auto-trade failed for %s: %s", market_id, exc, exc_info=True)

        await manager.broadcast({"type": "new_signal", "data": signal.__dict__})
        self.last_analyzed[market_id] = datetime.utcnow()
        logger.info("Generated signal %s %s ev=%.2f", provider_name(), signal.signal, signal.expected_value)

        return signal

    def _pick_price_for_signal(self, signal: str, yes_price: float, no_price: float) -> float:
        sig = (signal or "").upper()
        if sig == "BUY_NO":
            return no_price or yes_price or 0.5
        return yes_price or no_price or 0.5

    def _direction_probability(self, signal: str, prob_yes: float) -> float:
        prob_yes = min(max(prob_yes, 0.0), 1.0)
        return 1.0 - prob_yes if (signal or "").upper() == "BUY_NO" else prob_yes

    def _normalized_stake(
        self,
        raw_stake: float,
        balance: float,
        available_to_deploy: float,
        open_positions: int,
        confidence: int,
        cfg=None,
    ) -> float:
        """
        Fractional Kelly stake sizing with hard balance reserve enforcement.
        - Base slot = deployable capital / max_open_positions
        - High-confidence (>=80) gets 1.5x slot
        - LLM suggestion is respected if it's within the slot (conservative is fine)
        - Hard floor: ₦100, hard cap: available_to_deploy
        """
        max_open = getattr(cfg, "max_open_positions", settings.agent_max_open_positions) if cfg else settings.agent_max_open_positions

        # Base slot = deployable capital divided equally across all slots
        base_slot = available_to_deploy / max_open if max_open > 0 else available_to_deploy

        # High conviction multiplier
        if confidence >= 80:
            base_slot = min(base_slot * 1.5, available_to_deploy)

        # Use LLM suggestion if it's reasonable and lower than our slot
        # (agent being conservative is fine; agent being aggressive is not)
        if raw_stake and 100 <= raw_stake < base_slot:
            base_slot = raw_stake

        # Hard caps: never exceed available_to_deploy or agent_max_position_size
        stake = min(base_slot, settings.agent_max_position_size, available_to_deploy)
        stake = max(stake, 100.0)  # ₦100 minimum
        return round(stake, 2)

    def _market_hotness(self, market: dict) -> float:
        liquidity = float(market.get("liquidity") or 0)
        volume = float(market.get("totalVolume") or market.get("volume") or 0)
        spread = abs((market.get("outcome1Price") or 0) - (market.get("outcome2Price") or 0))
        return liquidity * 0.1 + volume * 0.9 + max(0.0, 1 - spread)

    def _rank_signal(self, sig: SignalOutput, market_heat: float) -> float:
        heat_norm = min(market_heat, 10_000) / 10_000
        return (sig.expected_value or 0) * 0.6 + (sig.confidence or 0) * 0.3 + heat_norm * 0.1

    async def analyze_snipe(
        self,
        market_id: str,
        event: dict,
        seconds_remaining: float,
    ) -> Optional[SignalOutput]:
        """
        Snipe-specific analysis with live ticker data and timing decision.
        Returns SignalOutput with entry_timing and entry_delay_seconds populated.
        """
        event_id = event.get("id") or ""
        market: dict = {}
        for m in event.get("markets", []) or []:
            if m["id"] == market_id:
                market = m
                break
        if not market:
            return None

        title = market.get("title") or event.get("title") or market_id
        if title.strip().lower() in ("yes", "no", "yes or no"):
            title = event.get("title") or event.get("description", "")[:80] or market_id
        yes_price = float(market.get("outcome1Price") or 0.5)
        no_price = float(market.get("outcome2Price") or (1 - yes_price))
        description = event.get("description") or ""

        # Get live ticker for momentum — AMM markets return {} (not supported)
        ticker = await self.bayse.ticker(market_id, outcome="YES")
        # Fall back to market prices when ticker unavailable (AMM markets)
        last_price = ticker.get("lastPrice") or yes_price
        price_change_24h = ticker.get("priceChange24h") or 0.0
        volume_24h = ticker.get("volume24h") or 0
        ticker_available = bool(ticker)

        # Fetch search + RAG
        # For sniping, skip Tavily search (too slow) — use only cached RAG
        from app.services import rag as rag_service
        rag_chunks = rag_service.query(title, k=3)
        sources: list[str] = []
        snippets: list[str] = []

        prompt = (
            f"Market: {title}\n"
            f"Market ID: {market_id}\n"
            f"Description: {description or 'N/A'}\n"
            f"Current YES price: {yes_price:.4f}\n"
            f"Current NO price: {no_price:.4f}\n"
        )
        if ticker_available:
            prompt += (
                f"Last traded price: {last_price:.4f}\n"
                f"24h price change: {price_change_24h:+.4f}\n"
                f"24h volume: {volume_24h:.0f}\n"
            )
        else:
            prompt += "Note: Live ticker unavailable (AMM market) — use current prices above.\n"
        prompt += f"Time remaining: {seconds_remaining:.0f} seconds\n"
        if rag_chunks:
            prompt += "Background knowledge:\n" + "\n".join(f"  - {c}" for c in rag_chunks) + "\n"
        if snippets:
            prompt += "Recent news:\n" + "\n".join(f"  - {s}" for s in snippets) + "\n"
        if sources:
            prompt += f"Sources: {sources}\n"
        prompt += "\nReturn the trading signal JSON with entry_timing decision now."

        try:
            raw_text = await call_llm(prompt, system=SNIPE_SYSTEM_PROMPT)
            data = _extract_json(raw_text)
            data["market_id"] = market_id
            data["market_name"] = title
            output = SignalOutput(**data)

            wallet_balance = await self.bayse.get_wallet_balance()
            portfolio = await self.bayse.get_portfolio() or {}
            snipe_balance = wallet_balance if wallet_balance > 0 else float(
                portfolio.get("portfolioCurrentValue") or portfolio.get("availableBalance") or 0.0
            )
            deployed = float(portfolio.get("portfolioCost") or 0.0)
            reserve_pct = settings.agent_balance_reserve_pct
            # Count real open positions from portfolio
            open_pos = len([b for b in (portfolio.get("outcomeBalances") or []) if b])
            available_to_deploy = max(snipe_balance * (1 - reserve_pct) - deployed, 0.0) if snipe_balance > 0 else settings.agent_max_position_size
            output.suggested_stake = self._normalized_stake(
                output.suggested_stake, snipe_balance, available_to_deploy, open_pos, output.confidence, None
            )
            return output
        except Exception as exc:
            logger.warning("Snipe LLM call failed for %s: %s", market_id, exc)
            return None


_agent_instance: AIAgent | None = None


def get_agent() -> AIAgent:
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = AIAgent()
    return _agent_instance
