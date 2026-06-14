"""SQLite persistence for pipeline restart safety.

A long-running, human-in-the-loop pipeline can be interrupted at any phase gate
(crash, redeploy, owner taking hours to approve). Persisting each
:class:`ProjectState` lets the orchestrator resume exactly where it left off
instead of re-running expensive LLM/CAD work.

We store the state as a single JSON blob rather than a normalized schema: the
``ProjectState`` shape evolves with the product, and pydantic's
``model_dump_json``/``model_validate_json`` already give us a stable,
versionable serialization. JSON-in-SQLite keeps migrations trivial (no ALTER
TABLE churn) while still giving us durable, queryable-by-id storage.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.models import ProjectState


class StateStore:
    """Durable key/value store of ``ProjectState`` rows keyed by project id."""

    def __init__(self, db_path: str = "./data/state.db") -> None:
        """Open (creating if needed) the SQLite database at ``db_path``.

        Creates the parent directory and the ``projects`` table on first use.
        ``check_same_thread=False`` lets the single shared connection be used
        from the async orchestrator's worker threads.
        """
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY,
                data       TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def save(self, state: ProjectState) -> None:
        """Upsert ``state`` by its ``project_id``.

        Uses an INSERT ... ON CONFLICT upsert so repeated saves of the same
        project (one per stage transition) overwrite the prior snapshot.
        """
        self._conn.execute(
            """
            INSERT INTO projects (project_id, data, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                data = excluded.data,
                updated_at = excluded.updated_at
            """,
            (
                state.project_id,
                state.model_dump_json(),
                state.updated_at.isoformat(),
            ),
        )
        self._conn.commit()

    def load(self, project_id: str) -> ProjectState | None:
        """Return the stored :class:`ProjectState`, or ``None`` if absent."""
        cursor = self._conn.execute(
            "SELECT data FROM projects WHERE project_id = ?",
            (project_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return ProjectState.model_validate_json(row[0])

    def list_projects(self) -> list[str]:
        """Return all stored project ids, most recently updated first."""
        cursor = self._conn.execute(
            "SELECT project_id FROM projects ORDER BY updated_at DESC"
        )
        return [row[0] for row in cursor.fetchall()]

    def delete(self, project_id: str) -> None:
        """Remove the stored state for ``project_id`` (no-op if absent)."""
        self._conn.execute(
            "DELETE FROM projects WHERE project_id = ?",
            (project_id,),
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()
