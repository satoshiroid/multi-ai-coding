"""Tests for LLM provider factory: tier mapping, fallback, JSON extraction."""

from __future__ import annotations

import pytest

from src.llm.factory import TieredLLM, build_provider, build_tiered_llms
from src.llm.mock_provider import MockProvider
from src.llm.provider import LLMResponse, ProviderConfig, extract_json
from src.models import LlmMessage


def _settings() -> dict:
    return {
        "tiers": {
            "L1": {"provider": "anthropic", "model": "claude-x", "fallback": {"provider": "gemini", "model": "g"}},
            "L3": {"provider": "gemini", "model": "gemini-flash"},
        },
        "providers": {
            "anthropic": {"api_key_env": "NOPE_KEY", "max_tokens": 100},
            "gemini": {"api_key_env": "NOPE_KEY", "rate_limit_retries": 1},
        },
    }


def test_build_tiered_llms_force_mock():
    llms = build_tiered_llms(_settings(), force_mock=True)
    assert set(llms) == {"L1", "L3"}
    assert isinstance(llms["L1"], TieredLLM)
    assert isinstance(llms["L1"].primary, MockProvider)


def test_build_provider_unknown_raises():
    with pytest.raises(ValueError):
        build_provider("does-not-exist", "m", {})


def test_extract_json_bare():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    text = '```json\n{"confidence_score": 88, "summary": "ok"}\n```'
    assert extract_json(text)["confidence_score"] == 88


def test_extract_json_with_prose():
    text = 'Here is the result:\n{"x": {"y": 2}}\nThanks!'
    assert extract_json(text) == {"x": {"y": 2}}


def test_extract_json_no_object_raises():
    with pytest.raises(ValueError):
        extract_json("no json here")


@pytest.mark.asyncio
async def test_tiered_fallback_on_primary_error():
    class Boom(MockProvider):
        async def complete(self, messages, **opts):
            raise RuntimeError("primary down")

    primary = Boom()
    fallback = MockProvider()
    tiered = TieredLLM("L1", primary, fallback)
    resp = await tiered.complete([LlmMessage(role="user", content="design task")])
    assert isinstance(resp, LLMResponse)
    # fallback produced a valid response
    assert resp.text


@pytest.mark.asyncio
async def test_mock_provider_structured_output():
    provider = MockProvider()
    data = await provider.complete_structured(
        [LlmMessage(role="user", content="circuit design")]
    )
    assert "confidence_score" in data
    assert 0 <= data["confidence_score"] <= 100
