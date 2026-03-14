"""
FuturAgents — LLM Service (Anthropic Claude)
Farklı görevler için farklı Claude modelleri kullanır:
  - Opus   → Orchestrator / karar verici (pahalı ama doğru)
  - Sonnet → Analist agentlar (dengeli)
  - Haiku  → Hızlı veri özeti / teknik sinyal (ucuz)
"""
import json
import logging
from typing import Any, AsyncIterator

import anthropic
from anthropic import AsyncAnthropic

from app.core.config import settings

logger = logging.getLogger(__name__)

# Anthropic async client — uygulama ömrü boyunca tek instance
_anthropic_client: AsyncAnthropic | None = None


def get_anthropic_client() -> AsyncAnthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _anthropic_client


class LLMService:
    """
    Claude API sarmalayıcı.
    Structured JSON output, streaming ve tool-use destekli.
    """

    MODELS = {
        "orchestrator": settings.ANTHROPIC_MODEL,        # claude-opus-4-5
        "analyst":      settings.ANTHROPIC_SONNET_MODEL, # claude-sonnet-4-6
        "fast":         settings.ANTHROPIC_FAST_MODEL,   # claude-haiku-4-5
    }

    def __init__(self):
        self.client = get_anthropic_client()

    async def complete(
        self,
        system: str,
        user: str,
        model_tier: str = "analyst",   # "orchestrator" | "analyst" | "fast"
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> str:
        """Basit metin tamamlama"""
        model = self.MODELS[model_tier]
        msg = await self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return msg.content[0].text

    async def complete_json(
        self,
        system: str,
        user: str,
        model_tier: str = "analyst",
        max_tokens: int = 2048,
    ) -> dict:
        """
        JSON çıktı garantili tamamlama.
        System prompt'a JSON zorunluluğu eklenir.
        """
        json_system = system + "\n\nÖNEMLİ: Cevabını SADECE geçerli JSON formatında ver. Başka açıklama ekleme."
        raw = await self.complete(
            system=json_system,
            user=user,
            model_tier=model_tier,
            max_tokens=max_tokens,
            temperature=0.1,  # JSON için düşük temperature
        )
        # JSON bloğunu temizle
        cleaned = raw.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        return json.loads(cleaned.strip())

    async def stream(
        self,
        system: str,
        user: str,
        model_tier: str = "analyst",
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Streaming yanıt — SSE için kullanılır"""
        model = self.MODELS[model_tier]
        async with self.client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            async for text in stream.text_stream:
                yield text

    async def multi_turn(
        self,
        system: str,
        messages: list[dict],
        model_tier: str = "analyst",
        max_tokens: int = 2048,
    ) -> str:
        """Çok turlu konuşma — agent zincirinde kullanılır"""
        model = self.MODELS[model_tier]
        msg = await self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        return msg.content[0].text


# Singleton
_llm_service: LLMService | None = None


def get_llm_service() -> LLMService:
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
