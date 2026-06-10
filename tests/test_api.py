"""Tests for the FastAPI REST control plane (src/interfaces/api.py)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.interfaces.api import create_app


@pytest.fixture()
def client(tmp_path):
    """Test client wired to a temporary SQLite DB and force_mock=True."""
    import os
    os.environ.setdefault("STATE_DB_PATH", str(tmp_path / "state.db"))
    app = create_app(force_mock=True)
    with TestClient(app) as c:
        yield c


def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_list_projects_empty(client):
    resp = client.get("/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert "projects" in data
    assert isinstance(data["projects"], list)


def test_get_project_not_found(client):
    resp = client.get("/projects/does-not-exist")
    assert resp.status_code == 404


def test_start_project_returns_started(client):
    resp = client.post("/projects", json={"requirement": "test", "project_id": "api-test-1"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["started"] is True
    assert "task_done" in data
