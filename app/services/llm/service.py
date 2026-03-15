"""
FuturAgents — LLM Service (Anthropic Claude)
"""
import json
import logging
from typing import AsyncIterator

from anthropic import AsyncAnthropic

from app.core.config import settings

logger = logging.getLogger(__name__)

_anthropic_client: AsyncAnthropic | None = None


def get_anthropic_client() -> AsyncAnthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _anthropic_client


def _get_model(tier: str) -> str:
    """Runtime'da model adını oku — import sırasında değil"""
    mapping = {
        "orchestrator": settings.ANTHROPIC_MODEL,
        "analyst":      settings.ANTHROPIC_SONNET_MODEL,
        "fast":         settings.ANTHROPIC_FAST_MODEL,
    }
    model = mapping.get(tier, settings.ANTHROPIC_SONNET_MODEL)
    logger.debug(f"LLM model seçildi: tier={tier} model={model}")
    return model


class LLMService:

    def __init__(self):
        self.client = get_anthropic_client()

    async def complete(
        self,
        system: str,
        user: str,
        model_tier: str = "analyst",
        max_tokens: int = 1024,
    ) -> str:
        model = _get_model(model_tier)
        logger.info(f"LLM çağrısı: model={model} tier={model_tier}")
        try:
            # temperature parametresi Claude 4 serisiyle uyumsuz — kaldırıldı
            msg = await self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return msg.content[0].text
        except Exception as e:
            logger.error(f"LLM hatası (model={model}): {e}")
            raise

    async def complete_json(
        self,
        system: str,
        user: str,
        model_tier: str = "analyst",
        max_tokens: int = 1024,
    ) -> dict:
        json_system = (
            system
            + "\n\nKRİTİK: Yanıtını SADECE ham JSON olarak ver. "
            "Markdown code block (```) kullanma. "
            "Açıklama ekleme. Sadece { } ile başlayan JSON."
        )
        raw = await self.complete(
            system=json_system,
            user=user,
            model_tier=model_tier,
            max_tokens=max_tokens,
        )
        cleaned = raw.strip()
        # Markdown bloklarını temizle
        if "```" in cleaned:
            import re
            cleaned = re.sub(r"```(?:json)?\s*", "", cleaned).strip()
        # İlk { ile son } arasını al
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1:
            cleaned = cleaned[start:end+1]
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse hatası: {e}\nHam yanıt: {raw[:200]}")
            return {"signal": "NEUTRAL", "confidence": 0, "error": f"JSON parse: {e}"}

    async def stream(
        self,
        system: str,
        user: str,
        model_tier: str = "analyst",
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        model = _get_model(model_tier)
        async with self.client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            async for text in stream.text_stream:
                yield text


_llm_service: LLMService | None = None


def get_llm_service() -> LLMService:
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
