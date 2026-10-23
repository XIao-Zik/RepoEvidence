from pathlib import Path

from repo_evidence.core.config import load_settings


def test_repository_product_config_needs_no_legacy_dataset_or_default_collection(
    tmp_path: Path,
) -> None:
    config = tmp_path / "repo-evidence.toml"
    config.write_text('data_dir="./state"\n', encoding="utf-8")

    settings = load_settings(config)

    assert settings.data_dir == tmp_path / "state"
    assert "dataset_path" not in settings.snapshot()
    assert "knowledge_collection_id" not in settings.snapshot()
