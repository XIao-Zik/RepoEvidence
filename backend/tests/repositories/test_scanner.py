from __future__ import annotations

from pathlib import Path

import pytest

from repo_evidence.repositories.scanner import RepositoryScanner
from repo_evidence.repositories.store import RepositoryPathError


def test_scan_indexes_text_and_excludes_sensitive_dependency_and_binary_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sample-api"
    (root / "src").mkdir(parents=True)
    (root / "node_modules").mkdir()
    (root / "keys").mkdir()
    (root / "README.md").write_text("# Sample API\n", encoding="utf-8")
    (root / "src/auth.py").write_text("def authenticate():\n    return True\n", encoding="utf-8")
    (root / ".env").write_text("API_KEY=not-for-indexing\n", encoding="utf-8")
    (root / "node_modules/index.js").write_text("export {}\n", encoding="utf-8")
    (root / "keys/server.pem").write_text("-----BEGIN PRIVATE KEY-----\n", encoding="utf-8")
    (root / "image.bin").write_bytes(b"\x00\x01")

    result = RepositoryScanner().scan(root)

    assert [item.path for item in result.files] == ["README.md", "src/auth.py"]
    assert {item.path for item in result.excluded} == {
        ".env",
        "image.bin",
        "keys/server.pem",
        "node_modules",
    }
    assert len(result.manifest_sha256) == 64


def test_resolve_under_root_rejects_parent_and_symlink_escapes(tmp_path: Path) -> None:
    root = tmp_path / "sample-api"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("secret", encoding="utf-8")
    (root / "escape.py").symlink_to(outside)
    scanner = RepositoryScanner()

    with pytest.raises(RepositoryPathError, match="relative"):
        scanner.resolve_under_root(root, "../outside.py")
    with pytest.raises(RepositoryPathError, match="allowed root"):
        scanner.resolve_under_root(root, "escape.py")


def test_scanner_excludes_paths_listed_in_repoevidenceignore(tmp_path: Path) -> None:
    (tmp_path / ".repoevidenceignore").write_text("generated/\n*.log\n", encoding="utf-8")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "api.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "debug.log").write_text("safe\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")

    result = RepositoryScanner().scan(tmp_path)

    assert [item.path for item in result.files] == ["main.py"]
    assert {(item.path, item.reason) for item in result.excluded} >= {
        ("generated", "user ignore rule"),
        ("debug.log", "user ignore rule"),
    }
