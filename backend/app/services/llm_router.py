"""LLM Router — LiteLLM configuration for multi-model Vertex AI routing.

Supports two types of Vertex AI models:
  1. Gemini models (native): vertex_ai/gemini-*
  2. Model Garden (OpenAI-compat): deepseek-ai/*, moonshotai/*, etc.
     These use the OpenAI-compatible endpoint with Bearer token auth.
"""

import logging
import os

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from litellm import acompletion

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Model tier mapping: tier name → model identifier
MODEL_TIERS = {
    "fast": settings.llm_fast,
    "smart": settings.llm_smart,
    "reasoning": settings.llm_reasoning,
}

# Fallback chain: if primary fails, try next
FALLBACK_CHAIN = {
    "reasoning": ["smart", "fast"],
    "smart": ["fast"],
    "fast": [],
}

# Third-party Model Garden models need OpenAI-compatible routing.
# Map model prefix → region (some models are region-specific).
_MODEL_GARDEN_REGIONS = {
    "deepseek-ai/deepseek-v3.2-maas": "global",
    "deepseek-ai/deepseek-v3.1-maas": "us-west2",
    "deepseek-ai/deepseek-r1-0528-maas": "us-central1",
    "deepseek-ai/deepseek-ocr-maas": "global",
    "moonshotai/kimi-k2-thinking-maas": "global",
    "zai-org/glm-5-maas": "global",
    "zai-org/glm-4.7-maas": "global",
    "minimaxai/minimax-m2-maas": "global",
    "qwen/qwen3-next-80b-a3b-instruct-maas": "global",
    "openai/gpt-oss-120b-maas": "global",
    "openai/gpt-oss-20b-maas": "global",
    "meta/llama-3.3-70b-instruct-maas": "us-central1",
    "meta/llama-4-maverick-17b-128e-instruct-maas": "us-east5",
    "meta/llama-4-scout-17b-16e-instruct-maas": "us-east5",
}

# All available models with metadata for CEO selection
AVAILABLE_MODELS: list[dict] = [
    # Native Gemini
    {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "cost": "~$0.001", "type": "native", "tier_hint": "fast"},
    {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro", "cost": "~$0.010", "type": "native", "tier_hint": "reasoning"},
    {"id": "gemini-2.5-flash-lite", "name": "Gemini 2.5 Flash-Lite", "cost": "~$0.001", "type": "native", "tier_hint": "fast"},
    # Model Garden
    {"id": "deepseek-ai/deepseek-v3.2-maas", "name": "DeepSeek V3.2", "cost": "~$0.002", "type": "model_garden", "tier_hint": "smart"},
    {"id": "deepseek-ai/deepseek-v3.1-maas", "name": "DeepSeek V3.1", "cost": "~$0.002", "type": "model_garden", "tier_hint": "smart"},
    {"id": "deepseek-ai/deepseek-r1-0528-maas", "name": "DeepSeek R1", "cost": "~$0.005", "type": "model_garden", "tier_hint": "reasoning"},
    {"id": "deepseek-ai/deepseek-ocr-maas", "name": "DeepSeek OCR", "cost": "~$0.002", "type": "model_garden", "tier_hint": "smart"},
    {"id": "moonshotai/kimi-k2-thinking-maas", "name": "Kimi K2", "cost": "~$0.005", "type": "model_garden", "tier_hint": "reasoning"},
    {"id": "zai-org/glm-5-maas", "name": "GLM 5", "cost": "~$0.004", "type": "model_garden", "tier_hint": "smart"},
    {"id": "zai-org/glm-4.7-maas", "name": "GLM 4.7", "cost": "~$0.001", "type": "model_garden", "tier_hint": "fast"},
    {"id": "minimaxai/minimax-m2-maas", "name": "MiniMax M2", "cost": "~$0.002", "type": "model_garden", "tier_hint": "smart"},
    {"id": "qwen/qwen3-next-80b-a3b-instruct-maas", "name": "Qwen3-Next 80B", "cost": "~$0.001", "type": "model_garden", "tier_hint": "fast"},
    {"id": "openai/gpt-oss-120b-maas", "name": "GPT OSS 120B", "cost": "~$0.001", "type": "model_garden", "tier_hint": "smart"},
    {"id": "openai/gpt-oss-20b-maas", "name": "GPT OSS 20B", "cost": "~$0.001", "type": "model_garden", "tier_hint": "fast"},
    {"id": "meta/llama-3.3-70b-instruct-maas", "name": "Llama 3.3 70B", "cost": "~$0.001", "type": "model_garden", "tier_hint": "smart"},
    {"id": "meta/llama-4-maverick-17b-128e-instruct-maas", "name": "Llama 4 Maverick", "cost": "~$0.001", "type": "model_garden", "tier_hint": "smart"},
    {"id": "meta/llama-4-scout-17b-16e-instruct-maas", "name": "Llama 4 Scout", "cost": "~$0.001", "type": "model_garden", "tier_hint": "fast"},
]

# Cached GCP credentials for Model Garden auth
_gcp_creds = None


def _get_access_token() -> str:
    """Get a fresh GCP access token for Model Garden API calls."""
    global _gcp_creds
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not _gcp_creds and creds_path:
        _gcp_creds = service_account.Credentials.from_service_account_file(
            creds_path, scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    if _gcp_creds:
        _gcp_creds.refresh(Request())
        return _gcp_creds.token
    raise RuntimeError("No GCP credentials available for Model Garden auth")


def _is_model_garden(model: str) -> bool:
    """Check if a model is a third-party Model Garden model (not native Gemini)."""
    return "/" in model and not model.startswith("vertex_ai/")


def _build_kwargs(model: str, messages, temperature, max_tokens, tools) -> dict:
    """Build LiteLLM kwargs for either Gemini or Model Garden models."""
    if _is_model_garden(model):
        region = _MODEL_GARDEN_REGIONS.get(model, "global")
        if region == "global":
            endpoint_base = "aiplatform.googleapis.com"
        else:
            endpoint_base = f"{region}-aiplatform.googleapis.com"

        api_base = (
            f"https://{endpoint_base}/v1/projects/{settings.gcp_project_id}"
            f"/locations/{region}/endpoints/openapi"
        )
        token = _get_access_token()

        kwargs = {
            "model": f"openai/{model}",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "api_base": api_base,
            "api_key": "dummy",
            "extra_headers": {"Authorization": f"Bearer {token}"},
        }
    else:
        # Native Gemini model
        litellm_model = (
            model if model.startswith("vertex_ai/") else f"vertex_ai/{model}"
        )
        kwargs = {
            "model": litellm_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "vertex_project": settings.gcp_project_id,
            "vertex_location": settings.gcp_region,
        }

    if tools:
        kwargs["tools"] = tools
    return kwargs


async def chat(
    messages: list[dict],
    tier: str = "smart",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    tools: list[dict] | None = None,
    model_override: str | None = None,
    agent_id: str | None = None,
) -> dict:
    """Send a chat completion request using the specified model tier.

    If model_override is set, use that model directly instead of the tier mapping.
    Returns a dict with content, tool_calls, model, usage, finish_reason.
    """
    from app.services.circuit_breaker import circuit_registry, CircuitOpenError
    from app.services.cost_tracker import cost_tracker

    model_name = model_override or MODEL_TIERS.get(tier, MODEL_TIERS["smart"])
    kwargs = _build_kwargs(model_name, messages, temperature, max_tokens, tools)
    cb = circuit_registry.get_or_create(model_name)

    tried = [tier]
    try:
        response = await cb.call(acompletion, **kwargs)
        result = _parse_response(response, model_name)
        # Track cost
        usage = result.get("usage", {})
        await cost_tracker.record(
            agent_id=agent_id or "unknown",
            model=model_name,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )
        return result
    except CircuitOpenError:
        logger.warning(f"Circuit open for '{model_name}', skipping to fallback")
    except Exception as e:
        logger.warning(f"Model tier '{tier}' ({model_name}) failed: {e}")

    # Try fallback chain
    for fallback_tier in FALLBACK_CHAIN.get(tier, []):
        if fallback_tier in tried:
            continue
        tried.append(fallback_tier)
        fallback_model = MODEL_TIERS[fallback_tier]
        fallback_kwargs = _build_kwargs(
            fallback_model, messages, temperature, max_tokens, tools
        )
        fallback_cb = circuit_registry.get_or_create(fallback_model)
        try:
            logger.info(f"Falling back to '{fallback_tier}' ({fallback_model})")
            response = await fallback_cb.call(acompletion, **fallback_kwargs)
            result = _parse_response(response, fallback_model)
            usage = result.get("usage", {})
            await cost_tracker.record(
                agent_id=agent_id or "unknown",
                model=fallback_model,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            )
            return result
        except CircuitOpenError:
            logger.warning(f"Circuit open for fallback '{fallback_model}'")
        except Exception as e2:
            logger.warning(f"Fallback '{fallback_tier}' also failed: {e2}")
            continue
    raise RuntimeError(f"All model tiers failed. Tried: {tried}")


def _parse_response(response, model_name: str) -> dict:
    """Extract useful data from LiteLLM response."""
    choice = response.choices[0]
    content = choice.message.content or ""
    # Strip <think>...</think> blocks from thinking models
    if "<think>" in content:
        parts = content.split("</think>", 1)
        content = parts[-1].strip() if len(parts) > 1 else content
    usage = response.usage if response.usage else {}
    return {
        "content": content,
        "tool_calls": getattr(choice.message, "tool_calls", None),
        "model": model_name,
        "usage": {
            "input_tokens": getattr(usage, "prompt_tokens", 0),
            "output_tokens": getattr(usage, "completion_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
        },
        "finish_reason": choice.finish_reason,
    }
