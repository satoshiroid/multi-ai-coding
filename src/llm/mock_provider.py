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
        "project_type": "hardware",
        "summary": "要件を解析しマスター実行計画を策定",
        "domains": ["design", "mecha", "circuit", "software"],
        "subtasks": [],
        "confidence_score": 90,
        "artifacts": {},
        "metadata": {},
    },
    "senior": {
        "resolved": True,
        "guidance": "制約を見直し、寸法を明示して再生成してください",
        "escalate_to_owner": False,
        "reason": "",
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
        blob = " ".join(m.content for m in messages)

        # 1. The worker prompt's explicit task header is authoritative — the
        #    shared context may mention *other* domains (e.g. mecha constraints
        #    inside a circuit task at stage 8), so whole-blob keyword scanning
        #    misattributes those calls.
        header = re.search(r"# Task \((design|mecha|circuit|software)\)", blob)
        if header:
            return header.group(1)

        # 2. Role-specific schema markers (appended by complete_structured).
        if "escalate_to_owner" in blob:
            return "senior"
        if "project_type" in blob:
            return "pm"

        # 3. Fallback: keyword scan for free-form prompts.
        lowered = blob.lower()
        for key in ("design", "mecha", "circuit", "software", "pm"):
            if re.search(rf"\b{key}\b", lowered):
                return key
        if "回路" in lowered:
            return "circuit"
        if "メカ" in lowered or "筐体" in lowered:
            return "mecha"
        if "デザイン" in lowered or "意匠" in lowered:
            return "design"
        if "ファーム" in lowered or "ソフト" in lowered:
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
