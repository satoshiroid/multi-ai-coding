"""Gemini vision reviewer — scores a render image against a design brief.

Used by the live Blender modeling loop: a local code LLM builds the model, then
this reviewer (a multimodal cloud model) inspects the rendered PNG and returns
a structured verdict with concrete revision instructions.

Talks to the Generative Language REST API directly over httpx, so it works
even when the `google-genai` SDK is not installed (httpx is already a core
dependency). Free-tier 429s are retried with exponential backoff, mirroring
:class:`~src.llm.gemini_provider.GeminiProvider`.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import httpx

from src.llm.provider import extract_json

_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

_REVIEW_PROMPT = """あなたは工業デザインのアートディレクターです。
以下の要件に基づいて作られた3DモデルのレンダリングCG画像をレビューしてください。

# 要件
{brief}

# 評価観点
- 形状・プロポーションが要件に合っているか
- マテリアル・色・質感の完成度
- 構図（画像内にモデルが適切に収まっているか）
- 全体の説得力（コンセプトモデルとして見せられるか）

# 出力
次のJSONオブジェクトのみを返してください（マークダウン・前置き禁止）:
{{"score": <0-100の整数>, "approved": <scoreが{threshold}以上ならtrue>, \
"feedback": "<日本語で具体的な修正指示。形状/マテリアル/構図ごとに簡潔に>"}}"""


class GeminiVisionReviewer:
    """Review a rendered image against a brief; returns score + feedback."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash",
        score_threshold: int = 75,
        retries: int = 3,
        base_delay: float = 2.0,
        timeout: float = 60.0,
    ):
        self.api_key = api_key
        self.model = model
        self.score_threshold = score_threshold
        self._retries = retries
        self._base_delay = base_delay
        self._timeout = timeout

    async def review(self, brief: str, image_path: str) -> dict[str, Any]:
        """Score ``image_path`` against ``brief``.

        Returns ``{"score": int, "approved": bool, "feedback": str}``. A
        response the model mangles (no JSON) degrades to a zero-score,
        not-approved verdict carrying the raw text — the loop then revises
        rather than crashing.
        """
        image_b64 = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
        prompt = _REVIEW_PROMPT.format(brief=brief.strip(), threshold=self.score_threshold)
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": "image/png", "data": image_b64}},
                    ]
                }
            ],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024},
        }

        text = await self._generate(payload)
        try:
            data = extract_json(text)
        except ValueError:
            return {"score": 0, "approved": False, "feedback": text.strip()}

        score = self._clamp_score(data.get("score"))
        return {
            "score": score,
            # Trust the numeric score over the model's own boolean.
            "approved": score >= self.score_threshold,
            "feedback": str(data.get("feedback", "")).strip(),
        }

    async def _generate(self, payload: dict[str, Any]) -> str:
        """POST to generateContent with free-tier 429 backoff; return text."""
        url = f"{_API_BASE}/{self.model}:generateContent"
        headers = {"x-goog-api-key": self.api_key}

        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code == 429 and attempt < self._retries:
                    await asyncio.sleep(self._base_delay * (2**attempt))
                    continue
                resp.raise_for_status()
                return self._extract_text(resp.json())
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self._retries:
                    await asyncio.sleep(self._base_delay * (2**attempt))
                    continue
                raise
        raise RuntimeError(f"Gemini vision request failed: {last_error}")  # pragma: no cover

    @staticmethod
    def _extract_text(body: dict[str, Any]) -> str:
        """Join the text parts of the first candidate."""
        try:
            parts = body["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"Unexpected Gemini response shape: {json.dumps(body)[:500]}"
            ) from exc
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict))

    @staticmethod
    def _clamp_score(value: Any) -> int:
        try:
            return max(0, min(100, int(value)))
        except (TypeError, ValueError):
            return 0
