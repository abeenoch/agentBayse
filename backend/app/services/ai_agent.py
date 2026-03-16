import json
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.llm_client import get_llm_client, LLMClient
from app.services.risk_guard import risk_guard, check_trade_limits
from app.services.analysis import calculate_expected_value, calculate_implied_probability
from app.services.web_search import WebSearchService, get_search_service
from app.services.bayse_client import BayseClient, get_bayse_client
from app.services.storage import save_signal
from app.services.config_service import get_config
from app.websocket_manager import manager
from app.utils.logger import logger
from app.config import settings
from app.models.analysis_state import AnalysisState


BASE_JSON_SCHEMA = (
    '{"market_id":"...","market_name":"...","signal":"BUY_YES|BUY_NO|SELL|HOLD|AVOID",'
    '"confidence":0-100,"estimated_probability":0.0-1.0,"current_market_price":0-100,'
    '"expected_value":float,"reasoning":"<=280 chars","sources":["url1","url2"],'
    '"suggested_stake":float,"risk_level":"LOW|MEDIUM|HIGH"}'
)

AGENT_SYSTEM_PROMPT = f"""You are an expert Bayse Markets trader.
Respond with ONE compact JSON object only (no markdown, no prose, no code fences).
Schema (all required): {BASE_JSON_SCHEMA}
If you cannot comply, respond with {{"error":"refused"}} only."""


@dataclass
class AgentSignal:
    market_id: str
    market_name: str
    signal: str
    confidence: int
    estimated_probability: float
    current_market_price: float
    expected_value: float
    reasoning: str
    sources: list[str]
    suggested_stake: float
    risk_level: str


class AIAgent:
    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        search_service: Optional[WebSearchService] = None,
        bayse_client: Optional[BayseClient] = None,
    ):
        self.llm = llm or get_llm_client()
        self.search = search_service or get_search_service()
        self.bayse = bayse_client or get_bayse_client()
        self.last_analyzed: dict[str, datetime] = {}

    async def analyze_market(self, market_id: str, event: Optional[dict] = None, session: Optional[AsyncSession] = None) -> Optional[AgentSignal]:
        cfg = None
        if session is not None:
            from app.services.config_service import get_config  # local import to avoid cycle
            cfg = await get_config(session)
        # skip if recently analyzed
        now = datetime.utcnow()
        last = self.last_analyzed.get(market_id)
        if last and now - last < timedelta(minutes=settings.agent_reanalyze_minutes):
            logger.info("Skipping %s (analyzed %.1f min ago)", market_id, (now - last).total_seconds() / 60)
            return None
        # check DB persisted last analyzed
        if session is not None:
            state = await session.get(AnalysisState, market_id)
            if state and now - state.last_analyzed < timedelta(minutes=settings.agent_reanalyze_minutes):
                return None

        # 1) fetch market/event context
        event_data = event or await self.bayse.get_event(market_id)
        if not event_data:
            return None
        # pick matching market if nested
        market = None
        if isinstance(event_data, dict) and event_data.get("markets"):
            for m in event_data["markets"]:
                if m["id"] == market_id:
                    market = m
                    break
            if market is None:
                market = event_data["markets"][0]
        else:
            market = event_data

        title = market.get("title") or (event_data.get("title") if isinstance(event_data, dict) else None)
        yes_price = market.get("outcome1Price") or 0.5
        # 2) web search (lightweight)
        search_results = await self.search.search(f"{title} latest news", max_results=3)
        sources = [r.get("url") for r in search_results.get("results", [])][:3]

        # 3) LLM reasoning
        user_prompt = json.dumps(
            {
                "market_id": market_id,
                "market_name": title,
                "description": event_data.get("description"),
                "yes_price": yes_price,
                "no_price": market.get("outcome2Price"),
                "sources": sources,
            }
        )
        parsed = None
        llm_text = ""
        try:
            llm_text = await self.llm.generate(AGENT_SYSTEM_PROMPT, user_prompt)
            parsed = self._parse_json(llm_text)
            if not parsed:
                # retry with stricter prompt
                strict_prompt = (
                    "Return EXACTLY one JSON object matching this schema, nothing else: "
                    f"{BASE_JSON_SCHEMA}"
                )
                llm_text = await self.llm.generate(strict_prompt, user_prompt)
                parsed = self._parse_json(llm_text)
        except Exception as exc:
            logger.warning("LLM generation failed (%s); using fallback.", exc)

        if not parsed:
            logger.warning("LLM returned unparsable content; using heuristic. raw=%s", llm_text[:200])
            parsed = {
                "market_id": market_id,
                "market_name": title,
                "signal": "BUY_YES",
                "confidence": 75,
                "estimated_probability": max(0.65, calculate_implied_probability(yes_price * 100)),
                "current_market_price": yes_price * 100,
                "expected_value": None,
                "reasoning": llm_text[:240] if llm_text else "LLM unavailable",
                "sources": sources,
                "suggested_stake": 100,
                "risk_level": "MEDIUM",
            }

        # compute EV if missing
        if parsed.get("expected_value") is None:
            parsed["expected_value"] = calculate_expected_value(
                prob=parsed.get("estimated_probability", yes_price),
                price=parsed.get("current_market_price", yes_price * 100),
                stake=parsed.get("suggested_stake", 100) or 100,
            )

        signal = AgentSignal(**parsed)

        # 4) risk guard (uses portfolio and limits)
        portfolio = await self.bayse.get_portfolio()
        rg = risk_guard(signal.__dict__, portfolio)
        if not rg.passed:
            logger.info("Risk guard blocked signal %s reasons=%s", signal.market_id, rg.reasons)
            return None
        if cfg and cfg.balance_floor and portfolio:
            bal = portfolio.get("portfolioCurrentValue") or portfolio.get("availableBalance")
            if bal is not None and bal <= cfg.balance_floor:
                logger.info("Balance floor reached (%.2f<=%.2f); skipping trade", bal, cfg.balance_floor)
                return None
        if session is not None:
            tl = await check_trade_limits(session, cfg or await get_config(session))
            if not tl.passed:
                logger.info("Trade limit blocked signal %s reasons=%s", signal.market_id, tl.reasons)
                return None

        # 5) persist and broadcast
        if session is not None:
            await save_signal(session, signal.__dict__)
            # persist last analyzed
            db_state = await session.get(AnalysisState, market_id) or AnalysisState(market_id=market_id)
            db_state.last_analyzed = datetime.utcnow()
            session.add(db_state)
            await session.commit()
        await manager.broadcast({"type": "new_signal", "data": signal.__dict__})
        # record analysis time
        self.last_analyzed[market_id] = datetime.utcnow()

        return signal

    def _parse_json(self, text: str) -> Optional[dict]:
        try:
            return json.loads(text)
        except Exception:
            try:
                start = text.find("{")
                end = text.rfind("}")
                if start == -1 or end == -1:
                    return None
                return json.loads(text[start : end + 1])
            except Exception:
                return None


def get_agent() -> AIAgent:
    return AIAgent()
