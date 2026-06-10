"""The PM's persistent shared workspace across domains.

This mirrors the workflow PDF's "conversation variables" / Variable Assigner:
a single place where every domain worker's structured output accumulates so the
PM (and later stages) can reason over the whole project, not just the latest
worker's reply.

Two different merge semantics are deliberate:

* **Constraints use overwrite semantics.** A constraint is a *current truth*
  about the design (e.g. the PCB's max X dimension). When a domain re-runs and
  produces a newer value, the later value must replace the earlier one —
  keeping stale numbers around would let consistency checks pass against
  out-of-date geometry.
* **BOM uses append semantics.** The bill of materials is a *union* of parts
  contributed by each domain; every domain legitimately adds its own line
  items, so we accumulate rather than replace.
"""

from __future__ import annotations

from src.models import AgentResult, BomItem, Constraint, Domain


class ContextStore:
    """In-memory shared context for one project run.

    Holds the cross-domain constraints (keyed by name, overwrite-on-write) and
    the accumulated bill of materials (append-only). Worker results can be
    ingested wholesale via :meth:`ingest_result`.
    """

    def __init__(self) -> None:
        self._constraints: dict[str, Constraint] = {}
        self._bom: list[BomItem] = []

    # ------------------------------------------------------------------ #
    # Constraints (overwrite semantics — a later value replaces the earlier)
    # ------------------------------------------------------------------ #
    def set_constraint(self, constraint: Constraint) -> None:
        """Store/replace a constraint by its name.

        Overwrite (not append) because a constraint represents the single
        current value of a physical boundary condition; a fresh worker run
        supersedes the previous one.
        """
        self._constraints[constraint.name] = constraint

    def get_constraint(self, name: str) -> Constraint | None:
        """Return the constraint with ``name`` if present, else ``None``."""
        return self._constraints.get(name)

    def all_constraints(self) -> dict[str, Constraint]:
        """Return a shallow copy of all constraints keyed by name."""
        return dict(self._constraints)

    # ------------------------------------------------------------------ #
    # BOM (append semantics — each domain adds its parts)
    # ------------------------------------------------------------------ #
    def append_bom(self, items: list[BomItem]) -> None:
        """Merge parts into the bill of materials.

        The BOM is the union of every domain's contributions, but the same
        domain can legitimately run at multiple stages (e.g. circuit at the
        engineering and manufacturing stages) and re-list its parts — so a
        line is keyed by ``(domain, part_number)`` and a later entry *replaces*
        the earlier one rather than double-counting it.
        """
        for item in items:
            key = (item.domain, item.part_number)
            for i, existing in enumerate(self._bom):
                if (existing.domain, existing.part_number) == key:
                    self._bom[i] = item
                    break
            else:
                self._bom.append(item)

    def bom(self) -> list[BomItem]:
        """Return a shallow copy of the accumulated BOM."""
        return list(self._bom)

    def total_cost(self) -> float:
        """Return the summed ``line_cost`` of every BOM item, rounded to 2 dp."""
        return round(sum(item.line_cost for item in self._bom), 2)

    # ------------------------------------------------------------------ #
    # Result ingestion
    # ------------------------------------------------------------------ #
    def ingest_result(self, result: AgentResult) -> None:
        """Extract constraints and BOM entries from a worker result.

        Convention (kept loose so workers need not know the storage schema):

        * Any numeric ``result.metadata`` key ending in ``"_mm"`` becomes a
          :class:`Constraint` named after the key, owned by the result's domain.
        * ``result.metadata["bom"]`` may be a list of dicts; each is parsed into
          a :class:`BomItem` and appended.

        Parsing is intentionally defensive: malformed metadata from an LLM
        worker should never crash the orchestrator, so bad entries are skipped.
        """
        metadata = result.metadata if isinstance(result.metadata, dict) else {}

        # Dimension constraints: numeric "*_mm" keys.
        for key, value in metadata.items():
            if not isinstance(key, str) or not key.endswith("_mm"):
                continue
            # bool is a subclass of int — exclude it explicitly.
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            self.set_constraint(
                Constraint(
                    name=key,
                    value=float(value),
                    unit="mm",
                    owner_domain=result.domain,
                )
            )

        # BOM entries: list of dicts under the "bom" key.
        raw_bom = metadata.get("bom")
        if isinstance(raw_bom, list):
            parsed: list[BomItem] = []
            for entry in raw_bom:
                if not isinstance(entry, dict):
                    continue
                try:
                    parsed.append(self._parse_bom_entry(entry, result.domain))
                except Exception:
                    # Skip malformed line items rather than abort ingestion.
                    continue
            if parsed:
                self.append_bom(parsed)

    @staticmethod
    def _parse_bom_entry(entry: dict, default_domain: Domain) -> BomItem:
        """Build a :class:`BomItem` from a loose dict, defaulting the domain."""
        data = dict(entry)
        data.setdefault("domain", default_domain)
        return BomItem.model_validate(data)

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict:
        """Return a JSON-able summary of constraints, BOM and total cost.

        Used both for HITL display and as the input shape accepted by
        :meth:`ConsistencyChecker.check_from_constraints`.
        """
        return {
            "constraints": {
                name: constraint.model_dump(mode="json")
                for name, constraint in self._constraints.items()
            },
            "bom": [item.model_dump(mode="json") for item in self._bom],
            "total_cost": self.total_cost(),
        }
