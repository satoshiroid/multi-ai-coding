"""Cross-domain numeric consistency checks.

The PM's job is not only to collect each domain's output but to verify the
outputs fit together physically. The canonical check (from the workflow PDF's
PM consistency-check logic) is: *does the PCB actually fit inside the
mechanical enclosure with enough clearance?* — a class of bug that no single
domain worker can catch alone, because each only sees its own geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.models import AgentResult


@dataclass
class ConsistencyReport:
    """Outcome of a cross-domain check.

    ``details`` carries the actual numbers compared so the PM (or a human) can
    see *why* a check passed or failed without re-deriving it.
    """

    compatible: bool
    issues: list[str] = field(default_factory=list)
    details: dict[str, float] = field(default_factory=dict)


class ConsistencyChecker:
    """Validates that domain outputs are mutually compatible.

    ``clearance_margin_mm`` is the gap required on *each* side between the PCB
    edge and the enclosure inner wall; ``mount_tolerance_mm`` is reserved for
    mount-point alignment checks.
    """

    def __init__(
        self,
        clearance_margin_mm: float = 1.0,
        mount_tolerance_mm: float = 0.5,
    ) -> None:
        self.clearance_margin_mm = clearance_margin_mm
        self.mount_tolerance_mm = mount_tolerance_mm

    # ------------------------------------------------------------------ #
    # Primary check: enclosure vs PCB
    # ------------------------------------------------------------------ #
    def check_enclosure_vs_pcb(
        self,
        mecha_result: AgentResult,
        circuit_result: AgentResult,
    ) -> ConsistencyReport:
        """Check the PCB fits inside the enclosure inner cavity with clearance.

        Reads enclosure inner dimensions from ``mecha_result.metadata``
        (``inner_dim_x_mm``, ``inner_dim_y_mm``, ``inner_dim_z_mm``) and PCB
        dimensions from ``circuit_result.metadata`` (``pcb_dim_x_mm``,
        ``pcb_dim_y_mm``). The PCB fits on an axis when the inner dimension is
        at least the PCB dimension plus clearance on both sides.

        Missing keys are treated as failures with an explanatory issue rather
        than raising, since incomplete worker metadata is an expected real-world
        case the PM must surface, not crash on.
        """
        issues: list[str] = []
        details: dict[str, float] = {}

        mecha_meta = mecha_result.metadata if isinstance(mecha_result.metadata, dict) else {}
        circuit_meta = (
            circuit_result.metadata if isinstance(circuit_result.metadata, dict) else {}
        )

        inner_x = self._read_dim(mecha_meta, "inner_dim_x_mm", "enclosure inner X", issues, details)
        inner_y = self._read_dim(mecha_meta, "inner_dim_y_mm", "enclosure inner Y", issues, details)
        pcb_x = self._read_dim(circuit_meta, "pcb_dim_x_mm", "PCB X", issues, details)
        pcb_y = self._read_dim(circuit_meta, "pcb_dim_y_mm", "PCB Y", issues, details)

        # inner_dim_z is read opportunistically for the details payload only.
        inner_z = self._read_dim(
            mecha_meta, "inner_dim_z_mm", "enclosure inner Z", issues=None, details=details
        )
        if inner_z is not None:
            details["inner_dim_z_mm"] = inner_z

        margin = self.clearance_margin_mm
        details["clearance_margin_mm"] = margin

        # If any required dimension is missing we already recorded an issue;
        # report incompatible without a misleading numeric comparison.
        if None in (inner_x, inner_y, pcb_x, pcb_y):
            return ConsistencyReport(compatible=False, issues=issues, details=details)

        required_x = pcb_x + 2 * margin
        required_y = pcb_y + 2 * margin
        details["required_inner_x_mm"] = round(required_x, 4)
        details["required_inner_y_mm"] = round(required_y, 4)

        ok_x = inner_x >= required_x
        ok_y = inner_y >= required_y

        if not ok_x:
            issues.append(
                f"PCB does not fit on X axis: enclosure inner X {inner_x} mm < "
                f"required {required_x} mm (PCB {pcb_x} mm + 2x{margin} mm clearance)."
            )
        if not ok_y:
            issues.append(
                f"PCB does not fit on Y axis: enclosure inner Y {inner_y} mm < "
                f"required {required_y} mm (PCB {pcb_y} mm + 2x{margin} mm clearance)."
            )

        return ConsistencyReport(compatible=ok_x and ok_y, issues=issues, details=details)

    @staticmethod
    def _read_dim(
        meta: dict,
        key: str,
        label: str,
        issues: list[str] | None,
        details: dict[str, float],
    ) -> float | None:
        """Read a numeric dimension from metadata, recording issues/details.

        Returns the value as a float, or ``None`` if missing/non-numeric. When
        ``issues`` is provided, a missing/invalid value appends an explanatory
        issue (used for required dimensions); pass ``None`` for optional ones.
        """
        value = meta.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            if issues is not None:
                issues.append(f"Missing or non-numeric {label} ('{key}').")
            return None
        fvalue = float(value)
        details[key] = fvalue
        return fvalue

    # ------------------------------------------------------------------ #
    # Convenience: check from a ContextStore snapshot
    # ------------------------------------------------------------------ #
    def check_from_constraints(self, context_snapshot: dict) -> ConsistencyReport:
        """Same fit check, sourced from a :meth:`ContextStore.snapshot` dict.

        Reads the relevant ``*_mm`` constraints out of the snapshot's
        ``constraints`` map (each entry being a serialized ``Constraint`` with a
        ``value``). Lets the PM run consistency checks against the accumulated
        shared context rather than two specific worker results.
        """
        issues: list[str] = []
        details: dict[str, float] = {}

        constraints = {}
        if isinstance(context_snapshot, dict):
            raw = context_snapshot.get("constraints")
            if isinstance(raw, dict):
                constraints = raw

        def read(key: str, label: str) -> float | None:
            entry = constraints.get(key)
            value = entry.get("value") if isinstance(entry, dict) else None
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                issues.append(f"Missing or non-numeric {label} ('{key}').")
                return None
            fvalue = float(value)
            details[key] = fvalue
            return fvalue

        inner_x = read("inner_dim_x_mm", "enclosure inner X")
        inner_y = read("inner_dim_y_mm", "enclosure inner Y")
        pcb_x = read("pcb_dim_x_mm", "PCB X")
        pcb_y = read("pcb_dim_y_mm", "PCB Y")

        margin = self.clearance_margin_mm
        details["clearance_margin_mm"] = margin

        if None in (inner_x, inner_y, pcb_x, pcb_y):
            return ConsistencyReport(compatible=False, issues=issues, details=details)

        required_x = pcb_x + 2 * margin
        required_y = pcb_y + 2 * margin
        details["required_inner_x_mm"] = round(required_x, 4)
        details["required_inner_y_mm"] = round(required_y, 4)

        ok_x = inner_x >= required_x
        ok_y = inner_y >= required_y

        if not ok_x:
            issues.append(
                f"PCB does not fit on X axis: enclosure inner X {inner_x} mm < "
                f"required {required_x} mm (PCB {pcb_x} mm + 2x{margin} mm clearance)."
            )
        if not ok_y:
            issues.append(
                f"PCB does not fit on Y axis: enclosure inner Y {inner_y} mm < "
                f"required {required_y} mm (PCB {pcb_y} mm + 2x{margin} mm clearance)."
            )

        return ConsistencyReport(compatible=ok_x and ok_y, issues=issues, details=details)
