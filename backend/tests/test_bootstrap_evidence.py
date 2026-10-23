from __future__ import annotations

from pathlib import Path

from repo_evidence.bootstrap import create_workflow
from repo_evidence.core.config import ModelSettings, RepositoryRootSettings, Settings
from repo_evidence.core.llm.embedding.settings import EmbeddingSettings
from repo_evidence.workflows.evidence import EvidenceWorkflow


def test_bootstrap_creates_the_unified_evidence_workflow_for_allowed_roots(
    tmp_path: Path, monkeypatch
) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    settings = Settings(
        data_dir=tmp_path / "var",
        model=ModelSettings(provider="openai-compatible", api_key_env="TEST_MODEL_KEY"),
        embedding=EmbeddingSettings(
            name="test-embedding",
            base_url="http://localhost:11434/v1",
            dimensions=3,
            api_key_env="TEST_EMBEDDING_KEY",
            revision="test",
        ),
        knowledge_url_env="TEST_KNOWLEDGE_DSN",
        repository_roots=(RepositoryRootSettings("projects", projects),),
    )
    for key in ("TEST_MODEL_KEY", "TEST_EMBEDDING_KEY", "TEST_KNOWLEDGE_DSN"):
        monkeypatch.setenv(key, "fixture")

    workflow = create_workflow(settings)

    assert isinstance(workflow, EvidenceWorkflow)
    assert workflow.repository_tools.registry.roots["projects"].path == projects
