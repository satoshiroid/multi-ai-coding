"""End-to-end pipeline tests using the mock LLM/CAD/CLI stack."""

from __future__ import annotations

import pytest

from src.config import load_agents, load_settings
from src.models import StageStatus
from src.orchestrator.builder import build_orchestrator


def _cfg():
    return load_settings(), load_agents()


@pytest.mark.asyncio
async def test_full_pipeline_reaches_done():
    settings, agents = _cfg()
    orch = build_orchestrator(settings, agents, force_mock=True)
    state = await orch.run("Wi-Fi環境モニターを作りたい", project_id="e2e-1")
    assert state.status == StageStatus.DONE
    assert state.current_stage == 9
    # All four domains produced a result.
    assert set(state.results) == {"design", "mecha", "circuit", "software"}


@pytest.mark.asyncio
async def test_pipeline_collects_bom():
    settings, agents = _cfg()
    orch = build_orchestrator(settings, agents, force_mock=True)
    await orch.run("環境モニター", project_id="e2e-2")
    assert len(orch.context.bom()) >= 1
    assert orch.context.total_cost() > 0


@pytest.mark.asyncio
async def test_pipeline_persists_state(tmp_path):
    settings, agents = _cfg()
    db = str(tmp_path / "state.db")
    orch = build_orchestrator(settings, agents, state_db_path=db, force_mock=True)
    await orch.run("環境モニター", project_id="persist-1")

    # Reload from disk.
    from src.orchestrator.state_store import StateStore

    store = StateStore(db)
    try:
        loaded = store.load("persist-1")
        assert loaded is not None
        assert loaded.project_id == "persist-1"
        assert loaded.status == StageStatus.DONE
    finally:
        store.close()


@pytest.mark.asyncio
async def test_app_pipeline_selected_for_software_requirement():
    """A purely-software requirement should route through APP_PIPELINE."""
    from src.llm.factory import TieredLLM
    from src.llm.mock_provider import MockProvider

    def responder(messages):
        blob = " ".join(m.content for m in messages).lower()
        # PM prompt: return app project_type
        if any(k in blob for k in ["project_type", "decompose", "plan", "プロジェクト", "requirement"]):
            return {
                "project_type": "app",
                "summary": "ECサイトのモバイルアプリ開発",
                "domains": ["design", "software"],
                "subtasks": [],
            }
        # design/UI worker
        if "design" in blob or "デザイン" in blob or "ui" in blob:
            return {
                "summary": "UIデザインを生成",
                "confidence_score": 90,
                "artifacts": {"design_spec": {"screens": ["home", "cart"]}},
                "metadata": {"target_platform": "ios"},
            }
        # software worker (architecture + implementation)
        return {
            "summary": "アーキテクチャ設計完了",
            "confidence_score": 88,
            "artifacts": {},
            "metadata": {"tech_stack": "React Native"},
        }

    settings, agents = _cfg()
    orch = build_orchestrator(settings, agents, force_mock=True)
    controlled = TieredLLM("x", MockProvider(responder=responder))
    orch.pm.llm = controlled
    orch.senior.llm = controlled
    for w in orch.workers.values():
        w.llm = controlled

    state = await orch.run("ECサイトのモバイルアプリを作りたい", project_id="app-1")
    assert state.status == StageStatus.DONE
    assert state.project_type == "app"
    # App pipeline has 8 stages (final HITL is stage 8).
    assert state.current_stage == 8
    # Hardware domains must not appear in an app-only pipeline.
    assert "mecha" not in state.results
    assert "circuit" not in state.results
    assert "software" in state.results


@pytest.mark.asyncio
async def test_default_mock_routes_app_requirement_to_app_pipeline():
    """Regression: with the DEFAULT mock stack (no responder injection) an app
    requirement must select APP_PIPELINE — the live `--mock` run previously
    classified every project as hardware."""
    settings, agents = _cfg()
    orch = build_orchestrator(settings, agents, force_mock=True)
    state = await orch.run("ToDoリスト管理アプリを作りたい", project_id="app-mock-1")
    assert state.status == StageStatus.DONE
    assert state.project_type == "app"
    assert state.current_stage == 8
    assert "mecha" not in state.results
    assert "circuit" not in state.results


@pytest.mark.asyncio
async def test_low_confidence_triggers_escalation():
    """A worker returning low confidence should route through the senior agent."""
    from src.llm.factory import TieredLLM
    from src.llm.mock_provider import MockProvider

    # Mock that returns low confidence for design, high for everything else.
    def responder(messages):
        blob = " ".join(m.content for m in messages).lower()
        if "design" in blob or "意匠" in blob or "デザイン" in blob:
            return {"summary": "低信頼デザイン", "confidence_score": 30, "artifacts": {}, "metadata": {}}
        if "resolved" in blob or "guidance" in blob:  # senior advise schema hint
            return {"resolved": True, "guidance": "丸みを強調して再生成", "escalate_to_owner": False, "reason": ""}
        return {"summary": "ok", "confidence_score": 95, "artifacts": {}, "metadata": {}}

    settings, agents = _cfg()
    orch = build_orchestrator(settings, agents, force_mock=True)
    # Swap all tiers to a controlled mock.
    controlled = TieredLLM("x", MockProvider(responder=responder))
    orch.pm.llm = controlled
    orch.senior.llm = controlled
    for w in orch.workers.values():
        w.llm = controlled

    state = await orch.run("デザイン重視の製品", project_id="esc-1")
    # Pipeline still completes after escalation/retry.
    assert state.status == StageStatus.DONE
