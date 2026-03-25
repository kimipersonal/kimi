"""Vertex AI Provider Plugin — wraps existing LLM router as a plugin."""

from app.plugins.base import ProviderPlugin
from app.services import llm_router


class VertexAIProvider(ProviderPlugin):
    """Vertex AI provider — native Gemini + Model Garden models."""

    @property
    def name(self) -> str:
        return "vertex_ai"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "Google Vertex AI — Gemini models + Model Garden (DeepSeek, Llama, etc.)"

    def get_models(self) -> list[dict]:
        return llm_router.AVAILABLE_MODELS

    async def chat(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
    ) -> dict:
        return await llm_router.chat(
            messages=messages,
            model_override=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
        )

    async def get_embedding(self, text: str) -> list[float] | None:
        return await llm_router.get_embedding(text)

    def get_context_window(self, model: str) -> int:
        return llm_router.get_context_window(model=model)


def register(registry):
    """Register the Vertex AI provider plugin."""
    registry.register(VertexAIProvider())
