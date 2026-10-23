from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_product_package_and_console_script_are_repo_evidence() -> None:
    metadata = tomllib.loads((ROOT / "backend/pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["name"] == "repo-evidence"
    assert metadata["project"]["scripts"] == {
        "repo-evidence": "repo_evidence.cli:main",
    }
    assert (ROOT / "backend/src/repo_evidence/__init__.py").is_file()


def test_backend_container_starts_the_repo_evidence_factory() -> None:
    dockerfile = (ROOT / "infra/backend/Dockerfile").read_text(encoding="utf-8")

    assert "repo_evidence.cli:app_factory" in dockerfile
    assert "repo_evidence.cli:app_factory" in dockerfile
