import json
import asyncio
import httpx

from app.config import settings
from app.utils.logger import logger

try:  # optional Groq SDK; falls back to HTTP if unavailable
    from groq import Groq  # type: ignore
    _groq_available = True
except Exception:
    _groq_available = False


def provider_name() -> str:
    return settings.ai_provider.lower() if not settings.mock_mode else "mock"


async def call_llm(prompt: str, system: str = "") -> str:
    """Send a prompt to the configured LLM and return the text response."""
    p = provider_name()

    if p == "mock":
        return _mock_response()

    if p == "groq":
        # Prefer official SDK if installed; fallback to HTTP.
        if _groq_available:
            return await _call_groq_sdk(
                api_key=settings.groq_api_key,
                model=settings.groq_model,
                system=system,
                prompt=prompt,
            )
        return await _call_openai_compat(
            base_url="https://api.groq.com/openai/v1",
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            system=system,
            prompt=prompt,
        )

    if p == "openai":
        return await _call_openai_compat(
            base_url="https://api.openai.com/v1",
            api_key=settings.openai_api_key,
            model="gpt-4o-mini",
            system=system,
            prompt=prompt,
        )

    if p == "gemini":
        return await _call_gemini(system=system, prompt=prompt)

    if p == "anthropic":
        return await _call_anthropic(system=system, prompt=prompt)

    logger.warning("Unknown AI_PROVIDER '%s' — using mock response.", p)
    return _mock_response()


async def _call_openai_compat(
    base_url: str, api_key: str, model: str, system: str, prompt: str
) -> str:
    api_key = (api_key or "").strip()
    if not api_key:
        raise ValueError("Missing GROQ/OPENAI API key.")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"model": model, "messages": messages, "temperature": 0.2},
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "LLM HTTP %s for model=%s provider_url=%s body=%s",
                resp.status_code,
                model,
                base_url,
                resp.text[:500],
            )
            raise
        return resp.json()["choices"][0]["message"]["content"]


async def _call_gemini(system: str, prompt: str) -> str:
    combined = f"{system}\n\n{prompt}" if system else prompt
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent?key={settings.gemini_api_key}"
    )
    body = {"contents": [{"parts": [{"text": combined}]}]}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=body)
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


async def _call_anthropic(system: str, prompt: str) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": "claude-3-5-haiku-latest",
                "max_tokens": 1024,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]


async def _call_groq_sdk(api_key: str, model: str, system: str, prompt: str) -> str:
    api_key = (api_key or "").strip()
    if not api_key:
        raise ValueError("Missing GROQ API key.")

    def _run():
        client = Groq(api_key=api_key)
        completion = client.chat.completions.create(
            model=model,
            messages=[*( [{"role": "system", "content": system}] if system else [] ), {"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return completion.choices[0].message.content

    return await asyncio.to_thread(_run)


def _mock_response() -> str:
    return json.dumps({
        "market_id": "mock",
        "market_name": "Mock Market",
        "signal": "BUY_YES",
        "confidence": 75,
        "estimated_probability": 0.65,
        "current_market_price": 55.0,
        "expected_value": 7.5,
        "reasoning": "Mock signal — no real LLM key configured.",
        "sources": [],
        "suggested_stake": 100.0,
        "risk_level": "LOW",
    })
