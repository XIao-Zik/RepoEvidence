# 回归测试：配置加载与 HTTP 契约。测试中的固定输入用于复现边界，不作为业务答案来源。
import json
import time
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from repo_evidence.core.chat.types import SessionView
from repo_evidence.core.config import Settings, load_settings
from repo_evidence.core.event_log.types import Event
from repo_evidence.core.execution.manager import SessionManager
from repo_evidence.core.http import create_app
from repo_evidence.core.json import parse_json, require_object
from tests.core.fixtures import FixtureWorkflow


def wait_terminal(client: TestClient, sid: str) -> SessionView:
    for _ in range(300):
        result = TypeAdapter(SessionView).validate_json(client.get(f"/api/sessions/{sid}").content)
        if result["status"] != "running":
            return result
        time.sleep(0.01)
    raise AssertionError("run did not finish")


def test_configuration_is_relative_to_file_and_does_not_capture_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "local.toml"
    path.write_text('data_dir="./state"\n')
    monkeypatch.setenv("REPO_EVIDENCE_PORT", "8011")
    monkeypatch.setenv("REPO_EVIDENCE_MODEL_API_KEY", "not-to-be-recorded")
    settings = load_settings(path)
    assert settings.data_dir == tmp_path / "state"
    assert settings.port == 8011
    assert "not-to-be-recorded" not in json.dumps(settings.snapshot())
    path.write_text('data_dir="./state"\ntypo=1\n')
    with pytest.raises(ValueError, match="Unknown"):
        load_settings(path)


def test_configuration_uses_repo_evidence_environment_and_resolves_allowed_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "local.toml"
    config.write_text('data_dir="./state"\n', encoding="utf-8")
    monkeypatch.setenv("REPO_EVIDENCE_PORT", "8012")
    monkeypatch.setenv(
        "REPO_EVIDENCE_ALLOWED_ROOTS",
        '[{"id":"projects","path":"./projects"}]',
    )

    settings = load_settings(config)

    assert settings.port == 8012
    assert settings.model.api_key_env == "REPO_EVIDENCE_MODEL_API_KEY"
    assert settings.repository_roots[0].id == "projects"
    assert settings.repository_roots[0].path == tmp_path / "projects"


def test_http_lifecycle_export_and_restart(tmp_path: Path) -> None:
    settings = Settings(tmp_path, mock_delay_ms=0)
    with TestClient(create_app(settings, FixtureWorkflow(settings))) as client:
        assert client.get("/api/health").status_code == 200
        assert (
            require_object(require_object(parse_json(client.get("/api/meta").content))["workflow"])[
                "id"
            ]
            == "fixture"
        )
        sid = TypeAdapter(SessionView).validate_json(client.post("/api/sessions", json={}).content)[
            "id"
        ]
        assert (
            client.post(
                f"/api/sessions/{sid}/messages",
                json={
                    "request_id": "one",
                    "content": "question",
                },
            ).status_code
            == 202
        )
        assert wait_terminal(client, sid)["status"] == "waiting_user"
        assert (
            client.post(
                f"/api/sessions/{sid}/continue",
                json={
                    "request_id": "invalid",
                },
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"/api/sessions/{sid}/messages",
                json={
                    "request_id": "two",
                    "content": "acct_demo production req_demo_503",
                },
            ).status_code
            == 202
        )
        final = wait_terminal(client, sid)
        assert final["status"] == "completed"
        events = TypeAdapter(list[Event]).validate_json(
            client.get(f"/api/sessions/{sid}/events").content
        )
        exported = client.get(f"/api/sessions/{sid}/export")
        assert [parse_json(line) for line in exported.text.splitlines()] == events
        assert (
            client.get(f"/api/sessions/{sid}/events?after={events[-2]['seq']}").json()
            == events[-1:]
        )
        assert client.get("/api/sessions/not-found").status_code == 404
        assert (
            client.post(
                f"/api/sessions/{sid}/messages",
                json={
                    "request_id": "empty",
                    "content": "   ",
                },
            ).status_code
            == 422
        )
        assert client.get(f"/api/sessions/{sid}/events?after=-1").status_code == 422
    with TestClient(create_app(settings, FixtureWorkflow(settings))) as client:
        assert (
            TypeAdapter(SessionView).validate_json(client.get(f"/api/sessions/{sid}").content)[
                "messages"
            ]
            == final["messages"]
        )


def test_second_backend_cannot_open_same_runtime(tmp_path: Path) -> None:
    settings = Settings(tmp_path, mock_delay_ms=0)
    with TestClient(create_app(settings, FixtureWorkflow(settings))):
        with pytest.raises(RuntimeError, match="already has"):
            with TestClient(create_app(settings, FixtureWorkflow(settings))):
                pass


def test_model_options_from_toml_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "model.toml"
    path.write_text('data_dir="./state"\n[model.request_options]\nmax_tokens=2048\n')
    assert load_settings(path).model.request_options == {"max_tokens": 2048}
    monkeypatch.setenv("REPO_EVIDENCE_MODEL_REQUEST_OPTIONS", '{"thinking":{"type":"enabled"}}')
    assert load_settings(path).model.request_options == {"thinking": {"type": "enabled"}}
    monkeypatch.setenv("REPO_EVIDENCE_MODEL_REQUEST_OPTIONS", "[]")
    with pytest.raises(ValueError, match="must be an object"):
        load_settings(path)


def test_internal_key_error_is_not_reported_as_missing_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(tmp_path)
    app = create_app(settings, FixtureWorkflow(settings))
    with TestClient(app, raise_server_exceptions=False) as client:

        async def broken_listing():
            raise KeyError("internal field")

        monkeypatch.setattr(
            cast(SessionManager, app.state.manager), "list_sessions", broken_listing
        )
        assert client.get("/api/sessions").status_code == 500
        assert client.get("/api/sessions/missing").status_code == 404
