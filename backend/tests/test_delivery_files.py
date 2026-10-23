from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_ci_workflow_invokes_the_single_make_target() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "make ci" in workflow


def test_makefile_declares_ci_without_online_evaluation() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    ci_block = makefile.split("ci:", 1)[1].split("\n\n", 1)[0]

    assert "quality" in ci_block
    assert "eval" in ci_block
    assert "eval-online" not in ci_block


def test_makefile_requires_explicit_opt_in_for_online_evaluation() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    online_block = makefile[makefile.index("\neval-online:") : makefile.index("\nci:")]
    assert "REPO_EVIDENCE_RUN_ONLINE_EVAL" in online_block
    assert "repo_evidence.evaluation.online_runner" in online_block


def test_makefile_declares_the_offline_index_benchmark() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    benchmark_block = makefile[
        makefile.index("\nbenchmark-index:") : makefile.index("\neval-online:")
    ]
    assert "repo_evidence.evaluation.index_benchmark" in benchmark_block


def test_delivery_runs_from_the_host_that_owns_docker() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    deliver_block = makefile[makefile.index("\ndeliver:") : makefile.index("\ndb-up:")]

    assert "python3 -m scripts.delivery.deliver" in deliver_block
    assert "--remote $(GITHUB_REMOTE)" in deliver_block
    assert "BACKEND_RUN" not in deliver_block


def test_makefile_keeps_the_source_origin_separate_from_private_delivery() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    init_block = makefile[makefile.index("\ngithub-init:") : makefile.index("\ndeliver:")]

    assert "GITHUB_REMOTE ?= repoevidence" in makefile
    assert "--remote $(GITHUB_REMOTE)" in init_block
