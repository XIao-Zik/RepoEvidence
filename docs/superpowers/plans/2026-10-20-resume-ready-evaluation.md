# Resume-Ready Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reproducible real-Agent quality, latency and index-performance evaluation, then generate an honest resume-ready report.

**Architecture:** Keep the existing fixture evaluator unchanged for CI. Add typed online evaluation contracts and an HTTP runner that calls the public session API, and add a separate deterministic index fixture benchmark. Detailed data stays in ignored artifacts while reports have only aggregate metrics.

**Tech Stack:** Python 3.12, pytest, httpx, FastAPI, Docker Compose, Make.

**Spec:** `docs/superpowers/specs/2026-10-20-resume-ready-evaluation-design.md`

## Global Constraints

- CI does not call a model and needs no key.
- Online evaluation is opt-in and fails before any call if the target is not a ready current snapshot.
- At least 30 source-backed cases cover all six required categories.
- Raw model data remains under ignored `artifacts/evaluation/`.
- The performance benchmark never reads a user repository or uses network embeddings.
- No commit or push is created without user instruction.

---

### Task 1: Implement online evaluation contracts and scoring

**Files:**
- Create: `backend/src/repo_evidence/evaluation/online.py`
- Create: `backend/tests/evaluation/test_online.py`
- Modify: `backend/src/repo_evidence/repositories/evaluation.py`

**Interfaces:**
- Produces `OnlineCase`, `OnlineCaseResult`, `parse_citations(text)`, `load_online_cases(path, minimum_cases=30)`, `score_online_answer(case, answer, elapsed_ms)`, and `percentile(values, ratio)`.

- [ ] **Step 1: Write failing tests**

```python
def test_score_requires_terms_and_supported_citations() -> None:
    result = score_online_answer(CASE, "search_repository\na.py:1-2", 10)
    assert result.strict_passed is True

def test_percentile_rejects_empty_values() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        percentile([], 0.5)
```

- [ ] **Step 2: Run to prove failure**

Run: `make test` scoped to `backend/tests/evaluation/test_online.py`.

Expected: imports fail.

- [ ] **Step 3: Implement minimal contracts and scorer**

Reuse existing `score_case` for range overlap, mandatory terms and forbidden paths. Parse only valid inclusive line references and calculate nearest-rank percentile. Results retain response text only for local artifact writers.

- [ ] **Step 4: Add loader tests and source-range validation**

Test duplicate IDs, missing expected citations, fewer than 30 cases and a cited end line past source EOF.

- [ ] **Step 5: Run focused tests and ruff**

Run: evaluation tests plus `ruff check backend/src/repo_evidence/evaluation backend/tests/evaluation`.

Expected: PASS.

### Task 2: Add the 30-case source-backed bank

**Files:**
- Create: `evaluation/cases/repository-evidence-online.json`
- Modify: `backend/tests/evaluation/test_online.py`

**Interfaces:**
- Each JSON case has `id`, `category`, `question`, `expected_citations`, `required_terms`, optional `forbidden_paths`.

- [ ] **Step 1: Write failing category/count test**

```python
def test_bank_has_thirty_cases_and_required_categories(repo_root: Path) -> None:
    cases = load_online_cases(repo_root / "evaluation/cases/repository-evidence-online.json")
    assert len(cases) >= 30
    assert {case.category for case in cases} >= REQUIRED_CATEGORIES
```

- [ ] **Step 2: Run it and confirm missing-bank failure.**

- [ ] **Step 3: Inspect source with `nl -ba` and add 30 Chinese questions**

Cover architecture, configuration, call chain, snapshot, error boundary and tool boundary. Use only facts the read-only Agent can establish and record every expected line range.

- [ ] **Step 4: Run all evaluation tests.**

Expected: all 30 cases parse and every referenced range exists.

### Task 3: Add opt-in HTTP runner and safe reports

**Files:**
- Create: `backend/src/repo_evidence/evaluation/online_runner.py`
- Create: `backend/tests/evaluation/test_online_runner.py`
- Modify: `Makefile`, `README.md`

**Interfaces:**
- Produces `run_online_evaluation(client, base_url, repository_id, collection_id, cases, artifact_dir, summary_path, poll_seconds, timeout_seconds)`.
- Uses `GET /api/health`, `GET /api/repositories/{id}`, and the session API.

- [ ] **Step 1: Write failing `httpx.MockTransport` tests**

```python
def test_runner_rejects_non_ready_snapshot_without_writing_report(tmp_path: Path) -> None:
    with pytest.raises(OnlineEvaluationPreflightError, match="ready"):
        run_online_evaluation(not_ready_client, "http://test", "r", "c", CASES, tmp_path, tmp_path / "r.md", 0, 1)
    assert not (tmp_path / "r.md").exists()
```

- [ ] **Step 2: Run test and confirm failure.**

- [ ] **Step 3: Implement preflight and per-case execution**

Require ready/current collection. Create a separate bound session, post a UUID request ID, poll a terminal session state, extract assistant text, score it, save raw detail, then delete the session. A timeout/failure is visible with an ID/reason and never contributes a successful latency.

- [ ] **Step 4: Implement reports and Make target**

Write raw answers only to `artifacts/evaluation/online-evaluation.json`. Generate `docs/quality/resume-evaluation.md` with suite hash, timestamp, model metadata if available, sample count, strict pass rate, citation metrics, P50/P95, failures and limitations. Add `eval-online`, protected by `REPO_EVIDENCE_RUN_ONLINE_EVAL=1`, and `evaluate-resume`; keep `ci: quality eval` unchanged.

- [ ] **Step 5: Run all runner tests, `make eval`, and check that no online request is made.**

### Task 4: Add deterministic index benchmark

**Files:**
- Create: `backend/src/repo_evidence/evaluation/index_benchmark.py`
- Create: `backend/tests/evaluation/test_index_benchmark.py`
- Modify: `Makefile`, `README.md`

**Interfaces:**
- Produces `run_index_benchmark(work_dir: Path, samples: int = 3)` and `make benchmark-index`.

- [ ] **Step 1: Write failing benchmark test**

```python
def test_benchmark_has_three_full_and_incremental_samples(tmp_path: Path) -> None:
    report = run_index_benchmark(tmp_path, samples=3)
    assert report.full.samples == report.incremental.samples == 3
    assert report.speedup_ratio > 0
```

- [ ] **Step 2: Run and confirm failure.**

- [ ] **Step 3: Implement deterministic fixture/timer**

Generate representative source/docs/config fixture, time three fresh full scans/corpus builds and three single-file-change incremental builds with fake local vector work. Include only the same safe scanner/chunker semantics and no configured network client.

- [ ] **Step 4: Produce artifacts and summary**

Write ignored `artifacts/evaluation/index-benchmark.json` and safe `docs/quality/index-benchmark.md` containing samples, median/P95, file/chunk counts, reuse/changed count, speed-up, platform and Python version.

- [ ] **Step 5: Run tests and `make benchmark-index`.**

### Task 5: Execute and audit the complete evaluation

**Files:**
- Generate: `docs/quality/resume-evaluation.md`, `docs/quality/index-benchmark.md`

- [ ] **Step 1: Verify Compose services and selected repository readiness without printing secrets.**
- [ ] **Step 2: Run `make eval && make benchmark-index && make test`.**
- [ ] **Step 3: Run `REPO_EVIDENCE_RUN_ONLINE_EVAL=1 make eval-online`.**
- [ ] **Step 4: Confirm detail files are ignored and Markdown contains no key or absolute path.**
- [ ] **Step 5: Run `make ci` and summarize actual measured results plus limitations.**

## Plan Self-Review

- Each spec requirement maps to Tasks 1–5.
- Types flow from question bank to scorer to HTTP runner; both reports use the same nearest-rank percentile.
