"""Tests for LLM provider factory: tier mapping, fallback, JSON extraction."""

from __future__ import annotations

import pytest

from src.llm.factory import (
    TieredLLM,
    _tier_env_override,
    build_provider,
    build_tiered_llms,
)
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


def test_tier_env_override_tier_specific(monkeypatch):
    monkeypatch.setenv("LLM_L3_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_L3_MODEL", "claude-opus-4-8")
    cfg = _tier_env_override("L3", {"provider": "gemini", "model": "gemini-flash"})
    assert cfg == {"provider": "anthropic", "model": "claude-opus-4-8"}


def test_tier_env_override_global_and_fallback(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setenv("LLM_L1_FALLBACK_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_L1_FALLBACK_MODEL", "gemini-2.0-flash")
    cfg = _tier_env_override(
        "L1", {"provider": "gemini", "model": "g", "fallback": {"provider": "gemini", "model": "g"}}
    )
    assert cfg["provider"] == "anthropic"
    assert cfg["model"] == "claude-haiku-4-5-20251001"
    assert cfg["fallback"] == {"provider": "gemini", "model": "gemini-2.0-flash"}


def test_tier_env_override_noop_without_env(monkeypatch):
    for var in ("LLM_PROVIDER", "LLM_MODEL", "LLM_L3_PROVIDER", "LLM_L3_MODEL"):
        monkeypatch.delenv(var, raising=False)
    original = {"provider": "gemini", "model": "gemini-flash"}
    assert _tier_env_override("L3", original) == original


def test_build_tiered_llms_honors_env_override(monkeypatch):
    monkeypatch.setenv("LLM_L3_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_L3_MODEL", "claude-x")
    llms = build_tiered_llms(_settings())
    assert type(llms["L3"].primary).__name__ == "AnthropicProvider"


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


@pytest.mark.asyncio
async def test_mock_infers_domain_from_task_header_not_context():
    """Stage-8 scenario: the context mentions mecha, but the task header is
    circuit — the canned circuit response must be returned, not mecha's."""
    from src.models import LlmMessage

    provider = MockProvider()
    prompt = (
        "# Task (circuit)\n承認済み仕様に基づき製造データを確定してください。\n\n"
        "# Context (shared constraints / prior results)\n"
        '{"constraints": {"inner_dim_x_mm": {"owner_domain": "mecha"}}}'
    )
    data = await provider.complete_structured([LlmMessage(role="user", content=prompt)])
    assert "pcb_dim_x_mm" in data.get("metadata", {})  # circuit canned response


@pytest.mark.asyncio
async def test_mock_infers_pm_from_schema_marker():
    """The PM planning prompt lists every domain name, but the project_type
    schema marker must route it to the pm canned response."""
    from src.models import LlmMessage

    provider = MockProvider()
    prompt = (
        "Decompose this. Domains: design, mecha, circuit, software.\n"
        'Schema: {"project_type": "hardware" | "app", ...}'
    )
    data = await provider.complete_structured([LlmMessage(role="user", content=prompt)])
    assert data.get("project_type") == "hardware"  # pm canned response


def _pm_prompt(requirement: str) -> str:
    """PM-planning-shaped prompt: the boilerplate mentions both project types,
    so detection must come from the requirement section alone."""
    return (
        "# Owner requirement\n"
        f"{requirement}\n\n"
        "# Your job\n"
        "Decompose this into a master development plan.\n"
        'project_type = "hardware" — physical devices, PCBs, firmware.\n'
        'project_type = "app" — purely software: web/mobile/desktop/CLI/SaaS.\n'
        "App: design (UI/UX) and software (backend/frontend/mobile)\n"
    )


@pytest.mark.asyncio
async def test_mock_pm_detects_app_requirement():
    """Regression: 'ToDoリスト管理アプリ' ran the hardware pipeline because the
    mock PM always returned project_type=hardware."""
    provider = MockProvider()
    data = await provider.complete_structured(
        [LlmMessage(role="user", content=_pm_prompt("ToDoリスト管理アプリを作りたい"))],
        schema_hint='{"project_type": "hardware" | "app", "domains": [str]}',
    )
    assert data["project_type"] == "app"
    assert data["domains"] == ["design", "software"]


@pytest.mark.asyncio
async def test_mock_pm_keeps_hardware_for_physical_product():
    provider = MockProvider()
    data = await provider.complete_structured(
        [LlmMessage(role="user", content=_pm_prompt("コンパクトなワイヤレスキーボードを作りたい"))],
        schema_hint='{"project_type": "hardware" | "app", "domains": [str]}',
    )
    assert data["project_type"] == "hardware"


@pytest.mark.asyncio
async def test_mock_pm_hardware_wins_mixed_requirement():
    """A device with a companion app is still a hardware project."""
    provider = MockProvider()
    data = await provider.complete_structured(
        [LlmMessage(role="user", content=_pm_prompt("スマホアプリ連携の温度センサーデバイスを作りたい"))],
        schema_hint='{"project_type": "hardware" | "app", "domains": [str]}',
    )
    assert data["project_type"] == "hardware"


@pytest.mark.asyncio
async def test_mock_infers_senior_from_schema_marker():
    from src.models import LlmMessage

    provider = MockProvider()
    prompt = (
        "# Escalated task (mecha)\n低信頼の結果をレビュー。\n"
        'Schema: {"resolved": bool, "guidance": str, "escalate_to_owner": bool}'
    )
    data = await provider.complete_structured([LlmMessage(role="user", content=prompt)])
    assert "guidance" in data and "escalate_to_owner" in data
