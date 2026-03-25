"""Tests for the LLM router — tier mapping, model validation, context windows."""


import pytest

from app.services.llm_router import (
    MODEL_TIERS,
    FALLBACK_CHAIN,
    AVAILABLE_MODELS,
    update_tier,
    get_context_window,
    _is_model_garden,
)


class TestModelTiers:
    def test_default_tiers_exist(self):
        assert "fast" in MODEL_TIERS
        assert "smart" in MODEL_TIERS
        assert "reasoning" in MODEL_TIERS

    def test_fallback_chain(self):
        assert "smart" in FALLBACK_CHAIN["reasoning"]
        assert "fast" in FALLBACK_CHAIN["reasoning"]
        assert "fast" in FALLBACK_CHAIN["smart"]
        assert FALLBACK_CHAIN["fast"] == []

    def test_available_models_not_empty(self):
        assert len(AVAILABLE_MODELS) > 10

    def test_all_models_have_required_fields(self):
        for m in AVAILABLE_MODELS:
            assert "id" in m
            assert "name" in m
            assert "type" in m
            assert "tier_hint" in m


class TestUpdateTier:
    def test_update_valid_tier(self):
        original = MODEL_TIERS["fast"]
        try:
            # Pick a valid model ID
            valid_id = AVAILABLE_MODELS[0]["id"]
            update_tier("fast", valid_id)
            assert MODEL_TIERS["fast"] == valid_id
        finally:
            MODEL_TIERS["fast"] = original

    def test_update_invalid_tier(self):
        with pytest.raises(ValueError, match="Unknown tier"):
            update_tier("nonexistent", "some-model")

    def test_update_invalid_model(self):
        with pytest.raises(ValueError, match="Unknown model"):
            update_tier("fast", "nonexistent-model-id")


class TestModelDetection:
    def test_native_gemini(self):
        assert _is_model_garden("gemini-2.5-flash") is False
        assert _is_model_garden("vertex_ai/gemini-2.5-pro") is False

    def test_model_garden(self):
        assert _is_model_garden("deepseek-ai/deepseek-v3.2-maas") is True
        assert _is_model_garden("meta/llama-3.3-70b-instruct-maas") is True


class TestContextWindow:
    def test_known_model(self):
        window = get_context_window(model="gemini-2.5-flash")
        assert window == 1_048_576

    def test_unknown_model_returns_default(self):
        window = get_context_window(model="unknown-model")
        assert window == 131_072

    def test_by_tier(self):
        window = get_context_window(tier="smart")
        assert window > 0

    def test_all_available_models_have_context_window(self):
        for m in AVAILABLE_MODELS:
            # Every model should have an entry or use default
            window = get_context_window(model=m["id"])
            assert window > 0
