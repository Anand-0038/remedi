"""API health / version gates (fixture mode)."""

import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remedi import __version__
from remedi.config import Settings
from remedi.api.app import _resolve_web_dir, create_app


def test_health_gates_and_version():
    client = TestClient(create_app())
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["product"] == "remedi"
    assert body["version"] == __version__
    assert body["gates"]["fixture_demo_ok"] is True
    assert body["gates"]["selftest_path"] == "/api/selftest"


def test_runtime_version_matches_project_metadata():
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    assert __version__ == pyproject["project"]["version"]


def test_root_serves_ui():
    client = TestClient(create_app())
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_web_assets_resolve_from_runtime_working_directory(
    tmp_path,
    monkeypatch,
):
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert _resolve_web_dir() == web_dir


def test_run_endpoint_cannot_bypass_sealed_apply_boundary():
    client = TestClient(create_app())
    r = client.post(
        "/api/run",
        json={"incident_id": "freshness-nyc-taxi", "dry_run": False},
    )
    assert r.status_code == 400
    assert "Propose first" in r.json()["detail"]


def test_live_mode_requires_application_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "remedi.api.app.get_settings",
        lambda: Settings(remedi_mode="live", remedi_api_key=""),
    )

    with pytest.raises(RuntimeError, match="REMEDI_API_KEY"):
        create_app()


def test_live_api_routes_require_valid_key(monkeypatch: pytest.MonkeyPatch):
    class FakeOrchestrator:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def list_incidents(self) -> list[object]:
            return []

    monkeypatch.setattr(
        "remedi.api.app.get_settings",
        lambda: Settings(remedi_mode="live", remedi_api_key="secret-test-key"),
    )
    monkeypatch.setattr("remedi.api.app.RemediOrchestrator", FakeOrchestrator)
    client = TestClient(create_app())

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/incidents").status_code == 401
    assert (
        client.get(
            "/api/incidents",
            headers={"Authorization": "Bearer secret-test-key"},
        ).status_code
        == 200
    )


def test_fixture_webhook_requires_application_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "remedi.api.app.get_settings",
        lambda: Settings(
            remedi_mode="fixture",
            remedi_api_key="",
            remedi_ops_webhook_url="https://hooks.example.invalid/remedi",
        ),
    )

    with pytest.raises(RuntimeError, match="REMEDI_API_KEY"):
        create_app()


def test_fixture_webhook_protects_api_routes(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "remedi.api.app.get_settings",
        lambda: Settings(
            remedi_mode="fixture",
            remedi_api_key="secret-test-key",
            remedi_ops_webhook_url="https://hooks.example.invalid/remedi",
        ),
    )
    client = TestClient(create_app())

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["gates"]["api_auth_required"] is True
    assert health.json()["gates"]["external_action_configured"] is True
    assert client.get("/api/incidents").status_code == 401
    assert (
        client.get(
            "/api/incidents",
            headers={"X-API-Key": "secret-test-key"},
        ).status_code
        == 200
    )
