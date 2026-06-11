"""Tests for the Gemini vision design reviewer (no network — _generate stubbed)."""

from __future__ import annotations

import pytest

from src.llm.gemini_vision import GeminiVisionReviewer


class StubReviewer(GeminiVisionReviewer):
    """Reviewer with the HTTP layer replaced by a canned text response."""

    def __init__(self, text: str, threshold: int = 75):
        super().__init__(api_key="test-key", score_threshold=threshold)
        self._text = text

    async def _generate(self, payload):  # noqa: ARG002 - signature parity
        return self._text


@pytest.fixture
def png(tmp_path):
    path = tmp_path / "render.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nfakedata")
    return str(path)


async def test_review_parses_json_verdict(png):
    reviewer = StubReviewer('{"score": 82, "approved": true, "feedback": "良好です"}')
    verdict = await reviewer.review("キーボード", png)
    assert verdict == {"score": 82, "approved": True, "feedback": "良好です"}


async def test_review_trusts_score_over_model_boolean(png):
    # Model says approved=false but the numeric score clears the threshold.
    reviewer = StubReviewer('{"score": 90, "approved": false, "feedback": "ok"}')
    verdict = await reviewer.review("brief", png)
    assert verdict["approved"] is True


async def test_review_below_threshold_not_approved(png):
    reviewer = StubReviewer('{"score": 40, "approved": true, "feedback": "形状が違う"}')
    verdict = await reviewer.review("brief", png)
    assert verdict["approved"] is False
    assert verdict["score"] == 40


async def test_review_handles_fenced_json(png):
    reviewer = StubReviewer('```json\n{"score": 75, "approved": true, "feedback": "f"}\n```')
    verdict = await reviewer.review("brief", png)
    assert verdict["score"] == 75
    assert verdict["approved"] is True


async def test_review_degrades_on_non_json(png):
    reviewer = StubReviewer("ごめんなさい、評価できませんでした。")
    verdict = await reviewer.review("brief", png)
    assert verdict["score"] == 0
    assert verdict["approved"] is False
    assert "評価できません" in verdict["feedback"]


async def test_review_clamps_out_of_range_score(png):
    reviewer = StubReviewer('{"score": 150, "approved": true, "feedback": "f"}')
    verdict = await reviewer.review("brief", png)
    assert verdict["score"] == 100


def test_extract_text_joins_parts():
    body = {
        "candidates": [
            {"content": {"parts": [{"text": '{"score": '}, {"text": "1}"}]}}
        ]
    }
    assert GeminiVisionReviewer._extract_text(body) == '{"score": 1}'


def test_extract_text_bad_shape_raises():
    with pytest.raises(RuntimeError):
        GeminiVisionReviewer._extract_text({"error": {"message": "bad key"}})
