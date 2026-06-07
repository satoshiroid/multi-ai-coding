"""Deterministic mock provider for tests and `--mock` E2E runs.

Returns canned, schema-valid responses keyed by the domain/role mentioned in
the prompt so the full pipeline can run without any API keys, network, or CAD.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from src.llm.provider import LLMProvider, LLMResponse, ProviderConfig
from src.models import LlmMessage


# Default canned structured outputs per domain keyword.
_DEFAULT_RESPONSES: dict[str, dict[str, Any]] = {
    "design": {
        "summary": "通気孔付きの丸みを帯びた小型筐体コンセプトを生成",
        "confidence_score": 88,
        "artifacts": {"render_image": "design_concept.png"},
        "metadata": {"style": "rounded-minimal"},
    },
    "mecha": {
        "summary": "スプレッドシート駆動でパラメトリック筐体を作成",
        "confidence_score": 84,
        "artifacts": {"step_file": "enclosure.step"},
        "metadata": {
            "inner_dim_x_mm": 100.0,
            "inner_dim_y_mm": 70.0,
            "inner_dim_z_mm": 25.0,
        },
    },
    "circuit": {
        "summary": "MCUBlock + LDOBlock + USBConnector で回路を構成",
        "confidence_score": 86,
        "artifacts": {"schematic_pdf": "schematic.pdf", "gerber": "gerber.zip"},
        "metadata": {
            "pcb_dim_x_mm": 98.0,
            "pcb_dim_y_mm": 68.0,
            "bom": [
                {
                    "domain": "circuit",
                    "part_number": "ESP32-WROOM",
                    "description": "Wi-Fi MCU module",
                    "quantity": 1,
                    "unit_cost": 3.5,
                    "supplier": "JLCPCB",
                }
            ],
        },
    },
    "software": {
        "summary": "RTOSスケルトンと周辺ドライバ、ユニットテストを生成",
        "confidence_score": 82,
        "artifacts": {"firmware_src": "firmware/"},
        "metadata": {"rtos": "FreeRTOS"},
    },
    "pm": {
        "summary": "要件を解析しマスター実行計画を策定",
        "confidence_score": 90,
        "artifacts": {},
        "metadata": {
            "domains": ["design", "mecha", "circuit", "software"],
        },
    },
}


class MockProvider(LLMProvider):
    """Provider that returns deterministic, schema-valid JSON.

    Tests can inject ``responder`` to fully control output.
    """

    def __init__(
        self,
        config: ProviderConfig | None = None,
        responder: Callable[[list[LlmMessage]], dict[str, Any]] | None = None,
    ):
        super().__init__(config or ProviderConfig(provider="mock", model="mock-1"))
        self._responder = responder
        self.calls: list[list[LlmMessage]] = []

    def _infer_domain(self, messages: list[LlmMessage]) -> str:
        blob = " ".join(m.content for m in messages).lower()
        # Prefer explicit english domain keywords, then japanese role hints.
        for key in ("design", "mecha", "circuit", "software", "pm"):
            if re.search(rf"\b{key}\b", blob):
                return key
        if "回路" in blob:
            return "circuit"
        if "メカ" in blob or "筐体" in blob:
            return "mecha"
        if "デザイン" in blob or "意匠" in blob:
            return "design"
        if "ファーム" in blob or "ソフト" in blob:
            return "software"
        return "pm"

    async def complete(self, messages: list[LlmMessage], **opts: Any) -> LLMResponse:
        self.calls.append(messages)
        if self._responder is not None:
            data = self._responder(messages)
        else:
            domain = self._infer_domain(messages)
            data = dict(_DEFAULT_RESPONSES.get(domain, _DEFAULT_RESPONSES["pm"]))
        return LLMResponse(
            text=json.dumps(data, ensure_ascii=False),
            provider=self.name,
            model=self.config.model,
            raw=data,
        )
