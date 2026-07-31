from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bayes_state import BayesState


def _normalize_key_part(value: object | None) -> str:
    text = str(value or "").strip().lower()
    text = text.replace(" ", "-").replace("_", "-")
    return "-".join(part for part in text.split("-") if part)


def _first_non_empty(*values: object | None) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def build_bayes_state_key_candidates(
    *,
    market_id: str | None = None,
    event_id: str | None = None,
    series_slug: str | None = None,
    category: str | None = None,
    default_key: str = "default",
) -> list[str]:
    """
    Build the lookup chain for a market-scoped Bayes prior.

    Most specific to least specific:
      market:<market_id> -> series:<series_slug> -> category:<category> -> default
    """
    candidates: list[str] = []

    market_part = _first_non_empty(market_id, event_id)
    if market_part:
        candidates.append(f"market:{_normalize_key_part(market_part)}")

    if series_slug:
        candidates.append(f"series:{_normalize_key_part(series_slug)}")

    if category:
        candidates.append(f"category:{_normalize_key_part(category)}")

    candidates.append(default_key or "default")

    seen: set[str] = set()
    ordered: list[str] = []
    for key in candidates:
        if key and key not in seen:
            ordered.append(key)
            seen.add(key)
    return ordered


async def resolve_bayes_state_key(
    session: AsyncSession,
    candidates: Iterable[str],
    *,
    default_key: str = "default",
) -> str:
    """
    Select the best available Bayes state key from a lookup chain.

    Preference order:
      1. Any candidate with historical updates.
      2. An existing candidate row, even if empty.
      3. default_key.
    """
    ordered = [key for key in candidates if key]
    if not ordered:
        return default_key

    result = await session.execute(
        select(BayesState).where(BayesState.state_key.in_(ordered))
    )
    states = list(result.scalars().all())
    if not states:
        return default_key

    by_key = {state.state_key: state for state in states}

    for key in ordered:
        state = by_key.get(key)
        if state and int(state.yes_updates or 0) + int(state.no_updates or 0) > 0:
            return key

    if default_key in by_key:
        return default_key

    return ordered[0]
