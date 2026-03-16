from typing import Literal, Optional
import httpx
import json

from app.config import settings
from app.utils.logger import logger


class LLMClient:
    def __init__(self):
        self.provider: Literal["gemini", "groq", "anthropic", "openai", "mock"] = (
            "mock" if settings.mock_mode else settings.ai_provider
        )
        # Force mock if keys are missing
        if self.provider == "groq" and not settings.groq_api_key:
            self.provider = "mock"
        if self.provider == "gemini" and not settings.gemini_api_key:
            self.provider = "mock"
        self.gemini_model = settings.gemini_model
        self.groq_model = settings.groq_model
        self._http = httpx.AsyncClient(timeout=30.0)

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        # Mock / fallback path
        if self.provider == "mock":
            return f"prob=0.6;reason=mocked signal for {user_prompt}"

        if self.provider == "gemini":
            return await self._call_gemini(system_prompt, user_prompt)
        if self.provider == "groq":
            return await self._call_groq(system_prompt, user_prompt)
        raise ValueError(f"Unsupported provider: {self.provider}")

    async def _call_gemini(self, system_prompt: str, user_prompt: str) -> str:
        if not settings.gemini_api_key:
            logger.warning("Gemini API key missing; falling back to mock response.")
            return "prob=0.55;reason=gemini key missing fallback"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent"
        headers = {"Content-Type": "application/json"}
        params = {"key": settings.gemini_api_key}
        body = {
            "contents": [
                {"role": "user", "parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": 180,
                "temperature": 0.2,
            },
        }
        try:
            resp = await self._http.post(url, params=params, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            return text
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                logger.warning("Gemini rate limited; switching to fallback.")
                return "prob=0.55;reason=gemini rate limit fallback"
            logger.warning("Gemini call failed (%s); using fallback.", exc.response.status_code)
            return "prob=0.55;reason=gemini http error fallback"
        except httpx.HTTPError as exc:
            logger.warning("Gemini call failed (%s); using fallback.", exc)
            return "prob=0.55;reason=gemini http error fallback"

    async def _call_groq(self, system_prompt: str, user_prompt: str) -> str:
        if not settings.groq_api_key:
            logger.warning("Groq API key missing; falling back to mock response.")
            return "prob=0.55;reason=groq key missing fallback"
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.groq_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.groq_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 140,
        }
        try:
            resp = await self._http.post(url, headers=headers, json=body)
            if resp.status_code == 400 and "model" in resp.text.lower():
                # auto-downgrade to a widely available model
                fallback_model = "llama-3-70b-8192"
                logger.warning("Groq model %s rejected; retrying with %s", self.groq_model, fallback_model)
                body["model"] = fallback_model
                resp = await self._http.post(url, headers=headers, json=body)
            if resp.status_code == 400 and "json_validate_failed" in resp.text:
                minimal_body = {
                    "model": body["model"],
                    "messages": [
                        {"role": "system", "content": "Return valid JSON only following the provided schema. No prose."},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.15,
                    "max_tokens": 120,
                }
                resp = await self._http.post(url, headers=headers, json=minimal_body)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            logger.warning("Groq call failed (%s): %s; using fallback.", exc.response.status_code, exc.response.text)
            return "prob=0.55;reason=groq http error fallback"
        except httpx.HTTPError as exc:
            logger.warning("Groq call failed (%s); using fallback.", exc)
            return "prob=0.55;reason=groq http error fallback"


def get_llm_client() -> LLMClient:
    return LLMClient()
