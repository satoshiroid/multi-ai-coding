"""L3 domain workers — the agents that actually do engineering work.

Each worker turns a :class:`TaskSpec` into a structured :class:`AgentResult`.
The ``confidence_score`` on that result is the escalation signal the L2 senior
relies on, so parsing here is deliberately defensive: a malformed or partial
LLM response must still yield a valid (low-confidence) result rather than
crashing the pipeline.
"""

from __future__ import annotations

import json
import os
from typing import Any

from src.agents.base_agent import BaseAgent
from src.llm.factory import TieredLLM
from src.models import AgentResult, Domain, TaskSpec

# JSON contract for non-design workers (no code allowed).
_RESULT_SCHEMA_HINT = (
    '{"summary": str, "confidence_score": int (0-100), '
    '"artifacts": object (compact specs only — NO code/scripts/long lists), '
    '"metadata": object (numeric dimensions in mm when applicable, e.g. inner_dim_x_mm)}'
)

# Design worker allows a short blender_script in artifacts.
_DESIGN_SCHEMA_HINT = (
    '{"summary": str, "confidence_score": int (0-100), '
    '"artifacts": {'
    '"sketch_prompts": [str, str, str] '
    '(2-3 DISTINCT design directions, each a vivid concise image-generation '
    'prompt describing the product concept for a design sketch — different forms/'
    'layouts, not minor variations), '
    '"design_spec": object (compact design spec)}, '
    '"metadata": object} '
    '(for software/app UI tasks, sketch_prompts describe UI mockups)'
)

# Implementation stages (task.allow_code=True): code files are allowed but must
# stay compact — the hard cap protects against max_tokens JSON truncation.
_CODE_SCHEMA_HINT = (
    '{"summary": str, "confidence_score": int (0-100), '
    '"artifacts": {"files": {"relative/path.ext": "file content (code)"}, '
    '"setup": str (brief setup/run instructions)}, '
    '"metadata": object} '
    '— keep total code under ~200 lines so the JSON is never truncated'
)


def _clamp_score(value: Any) -> int:
    """Coerce an arbitrary LLM value into a valid 0-100 confidence score.

    Anything non-numeric collapses to 0 (treated as "needs escalation") so a
    junk field never silently passes as high confidence.
    """
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


class BaseWorker(BaseAgent):
    """Common execution loop for all L3 workers; subclasses set ``domain``."""

    domain: Domain  # set by each concrete subclass

    def __init__(self, name: str, system_prompt: str, llm: TieredLLM):
        super().__init__(name=name, tier="L3", system_prompt=system_prompt, llm=llm)

    def _build_prompt(self, task: TaskSpec) -> str:
        """Render a task into a clear, sectioned prompt for the worker.

        Context and feedback are only included when present so the model isn't
        distracted by empty sections on a first-pass run.
        """
        sections = [f"# Task ({self.domain.value})", task.instruction.strip()]

        if task.context:
            # Pretty JSON keeps nested constraints/BOM readable for the model.
            context_json = json.dumps(task.context, ensure_ascii=False, indent=2)
            sections.append("# Context (shared constraints / prior results)")
            sections.append(context_json)

        if task.feedback:
            # Feedback means this is a re-run; make the revision request explicit.
            sections.append("# Feedback to address (this is a revision)")
            sections.append(task.feedback.strip())

        sections.append(
            "# Output\n"
            "Return your result as JSON. Be CONCISE — keep artifacts compact "
            "(key specs only, no exhaustive lists). Set confidence_score to your "
            "honest 0-100 confidence; a low score triggers senior review."
        )
        return "\n\n".join(sections)

    async def execute(self, task: TaskSpec) -> AgentResult:
        """Run the task and normalise the LLM output into an AgentResult."""
        prompt = self._build_prompt(task)
        schema_hint = _CODE_SCHEMA_HINT if task.allow_code else _RESULT_SCHEMA_HINT
        try:
            data = await self.run_structured(prompt, schema_hint=schema_hint)
        except (ValueError, json.JSONDecodeError) as exc:
            # Truncated / malformed JSON: return a zero-confidence result so the
            # pipeline continues and L2 escalation handles the retry.
            return AgentResult(
                task_id=task.task_id,
                domain=self.domain,
                summary=f"(JSON parse error — output was truncated: {exc})",
                confidence_score=0,
                artifacts={},
                metadata={"parse_error": str(exc)},
            )

        # Defensive extraction: never trust the model to return every field.
        summary = data.get("summary") or "(no summary produced)"
        artifacts = data.get("artifacts") or {}
        metadata = data.get("metadata") or {}

        return AgentResult(
            task_id=task.task_id,
            domain=self.domain,
            summary=str(summary),
            confidence_score=_clamp_score(data.get("confidence_score", 0)),
            artifacts=artifacts if isinstance(artifacts, dict) else {"value": artifacts},
            metadata=metadata if isinstance(metadata, dict) else {"value": metadata},
        )


class DesignWorker(BaseWorker):
    """Industrial design (Blender). Optionally renders via Blender MCP."""

    domain = Domain.DESIGN

    def __init__(
        self,
        name: str,
        system_prompt: str,
        llm: TieredLLM,
        blender_spec: Any = None,
        image_cfg: dict | None = None,
    ):
        super().__init__(name=name, system_prompt=system_prompt, llm=llm)
        self._blender_spec = blender_spec
        self._image_cfg = image_cfg or {}

    async def execute(self, task: TaskSpec) -> AgentResult:
        prompt = self._build_prompt(task)
        try:
            data = await self.run_structured(prompt, schema_hint=_DESIGN_SCHEMA_HINT)
        except (ValueError, json.JSONDecodeError) as exc:
            return AgentResult(
                task_id=task.task_id,
                domain=self.domain,
                summary=f"(JSON parse error: {exc})",
                confidence_score=0,
                artifacts={},
                metadata={"parse_error": str(exc)},
            )

        summary = data.get("summary") or "(no summary produced)"
        artifacts = data.get("artifacts") or {}
        metadata = data.get("metadata") or {}
        if not isinstance(artifacts, dict):
            artifacts = {"value": artifacts}
        if not isinstance(metadata, dict):
            metadata = {"value": metadata}

        result = AgentResult(
            task_id=task.task_id,
            domain=self.domain,
            summary=str(summary),
            confidence_score=_clamp_score(data.get("confidence_score", 0)),
            artifacts=artifacts,
            metadata=metadata,
        )

        # Primary: turn the design briefs into sketch images for the owner to
        # pick from (cheaper than generating Blender code).
        await self._generate_sketches(task, artifacts, result)

        # Backward-compat: still render a Blender script if one was produced.
        if self._blender_spec is not None and getattr(self._blender_spec, "enabled", False):
            for i, (label, script) in enumerate(self._variant_scripts(artifacts)[:3], start=1):
                await self._render_blender(task.task_id, script, result, index=i, label=label)

        return result

    async def _generate_sketches(
        self, task: TaskSpec, artifacts: dict, result: AgentResult
    ) -> None:
        """Generate sketch images from the design briefs (best effort)."""
        cfg = self._image_cfg
        if not cfg or not cfg.get("enabled"):
            return
        prompts = artifacts.get("sketch_prompts")
        if not isinstance(prompts, list) or not prompts:
            return
        from src.image_gen import ImageGenError, generate_images

        out_dir = os.path.join(os.getcwd(), "data", "sketches", task.task_id)
        try:
            paths = await generate_images(
                [str(p) for p in prompts[: int(cfg.get("count", 3))]],
                provider=cfg.get("provider"),
                model=cfg.get("model"),
                api_key=cfg.get("api_key"),
                out_dir=out_dir,
                size=cfg.get("size", "1024x1024"),
            )
        except ImageGenError as exc:
            result.metadata["image_gen_error"] = str(exc)
            return
        for i, path in enumerate(paths, start=1):
            result.artifacts[f"sketch_image_{i}"] = path
        result.metadata["sketch_count"] = len(paths)

    @staticmethod
    def _variant_scripts(artifacts: dict) -> list[tuple[str, str]]:
        """Extract (label, blender_script) pairs — multiple variants or a single."""
        out: list[tuple[str, str]] = []
        variants = artifacts.get("variants")
        if isinstance(variants, list):
            for i, v in enumerate(variants, start=1):
                if isinstance(v, dict) and v.get("blender_script"):
                    out.append((str(v.get("name") or f"案{i}"), str(v["blender_script"])))
        if not out and artifacts.get("blender_script"):  # backward-compatible single
            out.append(("案1", str(artifacts["blender_script"])))
        return out

    async def _render_blender(
        self,
        task_id: str,
        script: str,
        result: AgentResult,
        *,
        index: int | None = None,
        label: str | None = None,
    ) -> None:
        """Execute one script in Blender and attach its render path to result."""
        from src.mcp.blender_client import BlenderClient
        from src.mcp.client import client_for_spec

        render_dir = os.path.join(os.getcwd(), "data", "renders")
        os.makedirs(render_dir, exist_ok=True)
        suffix = f"_{index}" if index else ""
        render_path = os.path.join(render_dir, f"{task_id}{suffix}.png")
        err_key = f"blender_error_{index}" if index else "blender_error"

        # LLMs sometimes emit Unicode minus/dashes (− – —) where Python needs an
        # ASCII hyphen, which is a SyntaxError. Normalize before executing.
        script = script.translate(
            {0x2212: 0x2D, 0x2013: 0x2D, 0x2014: 0x2D, 0x2010: 0x2D, 0x2011: 0x2D, 0xFF0D: 0x2D}
        )

        try:
            async with BlenderClient(client_for_spec(self._blender_spec)) as bl:
                run_result = await bl.run_python(script)
                if not run_result.ok:
                    result.metadata[f"blender_script_error{suffix}"] = run_result.error
                    return
                render_result = await bl.render(render_path)
                if render_result.ok:
                    img_key = f"render_image_{index}" if index else "render_image"
                    result.artifacts[img_key] = render_path
                    result.metadata.setdefault("renders", []).append(
                        {"label": label or img_key, "path": render_path}
                    )
                    result.metadata["blender_rendered"] = True
                else:
                    result.metadata[f"blender_render_error{suffix}"] = render_result.error
        except Exception as exc:  # noqa: BLE001
            result.metadata[err_key] = str(exc)


class MechaWorker(BaseWorker):
    """Mechanical design (FreeCAD)."""

    domain = Domain.MECHA


class CircuitWorker(BaseWorker):
    """Circuit / PCB design (KiCAD)."""

    domain = Domain.CIRCUIT


class SoftwareWorker(BaseWorker):
    """Firmware (C/C++)."""

    domain = Domain.SOFTWARE


_WORKER_CLASSES: dict[Domain, type[BaseWorker]] = {
    Domain.DESIGN: DesignWorker,
    Domain.MECHA: MechaWorker,
    Domain.CIRCUIT: CircuitWorker,
    Domain.SOFTWARE: SoftwareWorker,
}


def build_worker(
    domain: Domain,
    system_prompt: str,
    llm: TieredLLM,
    name: str | None = None,
    blender_spec: Any = None,
    image_cfg: dict | None = None,
) -> BaseWorker:
    """Instantiate the worker for ``domain`` (defaults name to ``<domain>_worker``)."""
    cls = _WORKER_CLASSES.get(domain)
    if cls is None:
        raise ValueError(f"No worker registered for domain: {domain!r}")
    if domain == Domain.DESIGN:
        return cls(
            name=name or f"{domain.value}_worker",
            system_prompt=system_prompt,
            llm=llm,
            blender_spec=blender_spec,
            image_cfg=image_cfg,
        )
    return cls(name=name or f"{domain.value}_worker", system_prompt=system_prompt, llm=llm)
