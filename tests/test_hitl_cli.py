"""Tests for HITL manager + CLI channel (auto-approve and out-of-band resolve)."""

from __future__ import annotations

import asyncio

import pytest

from src.hitl import CliChannel, HitlManager
from src.hitl.channels.base_channel import BaseChannel
from src.models import HitlDecision, HitlRequest, HitlResponse


def _req(rid: str = "r1") -> HitlRequest:
    return HitlRequest(request_id=rid, project_id="p1", gate="g", title="t", body="b")


@pytest.mark.asyncio
async def test_cli_auto_approve_resolves_inline():
    mgr = HitlManager(CliChannel(auto_approve=True), timeout_hours=1)
    resp = await mgr.request("cli-p1", _req())
    assert resp.decision == HitlDecision.APPROVE


class _SilentChannel(BaseChannel):
    """A non-CLI channel that does nothing — decisions arrive via resolve()."""

    async def create_project_thread(self, project_id, title):
        return f"thread-{project_id}"

    async def push_approval(self, thread_id, request):
        return None

    async def push_escalation(self, thread_id, title, body, options):
        return None

    async def push_progress(self, thread_id, message):
        return None


@pytest.mark.asyncio
async def test_out_of_band_resolve():
    mgr = HitlManager(_SilentChannel(), timeout_hours=1)
    req = _req("async-1")

    async def resolver():
        await asyncio.sleep(0.05)
        ok = mgr.resolve(HitlResponse(request_id="async-1", decision=HitlDecision.REVISE, feedback="fix it"))
        assert ok

    resp, _ = await asyncio.gather(mgr.request("t", req), resolver())
    assert resp.decision == HitlDecision.REVISE
    assert resp.feedback == "fix it"


@pytest.mark.asyncio
async def test_timeout_returns_timeout_decision():
    mgr = HitlManager(_SilentChannel(), timeout_hours=0.0000001)  # ~0.36ms
    resp = await mgr.request("t", _req("to-1"))
    assert resp.decision == HitlDecision.TIMEOUT


@pytest.mark.asyncio
async def test_resolve_unknown_request_returns_false():
    mgr = HitlManager(_SilentChannel(), timeout_hours=1)
    assert mgr.resolve(HitlResponse(request_id="ghost", decision=HitlDecision.APPROVE)) is False


@pytest.mark.asyncio
async def test_channel_property_exposes_channel():
    ch = _SilentChannel()
    mgr = HitlManager(ch, timeout_hours=1)
    assert mgr.channel is ch


# ── CLI decision parsing ────────────────────────────────────────────────── #

def test_parse_decision_approve_tokens():
    for raw in ("a", "A", "", "approve", "承認"):
        resp = CliChannel._parse_decision("r1", raw)
        assert resp is not None and resp.decision == HitlDecision.APPROVE


def test_parse_decision_reject_tokens():
    for raw in ("x", "reject", "却下"):
        resp = CliChannel._parse_decision("r1", raw)
        assert resp is not None and resp.decision == HitlDecision.REJECT


def test_parse_decision_revise_with_feedback():
    resp = CliChannel._parse_decision("r1", "r 通気孔を追加して")
    assert resp is not None
    assert resp.decision == HitlDecision.REVISE
    assert resp.feedback == "通気孔を追加して"


def test_parse_decision_unrecognized_returns_none():
    """A typo must NOT silently approve — caller re-prompts on None."""
    for raw in ("z", "yes", "ok", "approv e"):
        assert CliChannel._parse_decision("r1", raw) is None
