from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_compose_allows_repo_evidence_to_use_non_conflicting_host_ports() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "${REPO_EVIDENCE_POSTGRES_PORT:-55432}" in compose
    assert "${REPO_EVIDENCE_BACKEND_PORT:-8000}" in compose
    assert "${REPO_EVIDENCE_FRONTEND_PORT:-5173}" in compose
