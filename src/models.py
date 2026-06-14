"""Shared Pydantic data models used across the orchestration system.

These types form the structured "message bus" between the PM (L1), senior
managers (L2) and domain workers (L3). Keeping them in one place ensures every
agent and tool speaks the same JSON contract — notably the ``confidence_score``
contract that drives escalation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# LLM messaging
# --------------------------------------------------------------------------- #
class LlmMessage(BaseModel):
    """A single chat message passed to an :class:`LLMProvider`."""

    role: Literal["system", "user", "assistant"]
    content: str


# --------------------------------------------------------------------------- #
# Domain enums
# --------------------------------------------------------------------------- #
class Domain(str, Enum):
    """Engineering domains handled by L3 workers."""

    DESIGN = "design"        # industrial design (Blender)
    MECHA = "mecha"          # mechanical design (FreeCAD)
    CIRCUIT = "circuit"      # circuit / PCB design (KiCAD)
    SOFTWARE = "software"    # firmware (C/C++)


class Tier(str, Enum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


# --------------------------------------------------------------------------- #
# Task / result contracts
# --------------------------------------------------------------------------- #
class TaskSpec(BaseModel):
    """A unit of work the PM delegates to a worker."""

    task_id: str
    domain: Domain
    instruction: str
    context: dict[str, Any] = Field(default_factory=dict)
    feedback: str | None = None          # owner/senior feedback on a re-run
    allow_code: bool = False             # implementation stages may emit code files
    created_at: datetime = Field(default_factory=_utcnow)


class AgentResult(BaseModel):
    """Standard worker output. ``confidence_score`` drives escalation."""

    task_id: str
    domain: Domain
    summary: str
    confidence_score: int = Field(ge=0, le=100)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)

    def needs_escalation(self, threshold: int) -> bool:
        return self.confidence_score < threshold


# --------------------------------------------------------------------------- #
# Bill of materials & physical constraints (shared context)
# --------------------------------------------------------------------------- #
class BomItem(BaseModel):
    """One line in the cross-domain bill of materials."""

    domain: Domain
    part_number: str
    description: str
    quantity: int = 1
    unit_cost: float = 0.0
    supplier: str | None = None

    @property
    def line_cost(self) -> float:
        return round(self.unit_cost * self.quantity, 4)


class Constraint(BaseModel):
    """A physical boundary condition shared between domains.

    Example: ``name="pcb_max_dimension_x"``, ``value=98.0``, ``unit="mm"``,
    ``owner_domain=Domain.CIRCUIT`` — produced by the circuit worker and
    consumed by the mecha worker.
    """

    name: str
    value: float
    unit: str = "mm"
    owner_domain: Domain
    updated_at: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------- #
# Human-in-the-loop
# --------------------------------------------------------------------------- #
class HitlDecision(str, Enum):
    APPROVE = "approve"
    REVISE = "revise"
    REJECT = "reject"
    TIMEOUT = "timeout"


class HitlRequest(BaseModel):
    """A request for an owner decision at a phase gate."""

    request_id: str
    project_id: str
    gate: str                            # e.g. "design_approval"
    title: str
    body: str
    image_paths: list[str] = Field(default_factory=list)
    bom: list[BomItem] = Field(default_factory=list)
    total_cost: float | None = None
    options: list[HitlDecision] = Field(
        default_factory=lambda: [HitlDecision.APPROVE, HitlDecision.REVISE, HitlDecision.REJECT]
    )
    created_at: datetime = Field(default_factory=_utcnow)


class HitlResponse(BaseModel):
    """The owner's answer to a :class:`HitlRequest`."""

    request_id: str
    decision: HitlDecision
    feedback: str | None = None
    responded_at: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------- #
# Pipeline state
# --------------------------------------------------------------------------- #
class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_HITL = "waiting_hitl"
    DONE = "done"
    FAILED = "failed"
    REJECTED = "rejected"


class ProjectState(BaseModel):
    """Persistable snapshot of a running pipeline (restart safety)."""

    project_id: str
    thread_id: str | None = None         # Discord forum thread id
    requirement: str = ""
    project_type: str = "hardware"       # "hardware" | "app" (PM decides at stage 2)
    current_stage: int = 0
    status: StageStatus = StageStatus.PENDING
    constraints: dict[str, Constraint] = Field(default_factory=dict)
    bom: list[BomItem] = Field(default_factory=list)
    results: dict[str, AgentResult] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=_utcnow)
