# RepoEvidence v1.1 Delivery, Evaluation, and Index Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reproducible delivery automation, developer-only RAG evaluation, and safe index operations without weakening evidence or repository boundaries.

**Architecture:** Keep product-facing index state in the existing repository registry/API, keep offline evaluation deterministic and outside the browser, and centralize local/CI delivery in `make ci`. Release automation is a separate guarded script that invokes the quality gate before staging, committing, and pushing.

**Tech Stack:** Python 3.12, FastAPI, SQLite, PostgreSQL/pgvector, React/TypeScript, Make, GitHub Actions, GitHub CLI.

**Spec:** `docs/superpowers/specs/2026-10-20-v1-1-delivery-evaluation-index-operations-design.md`

## Global Constraints

- Support WSL2, Linux, and macOS; browser code never sees host paths, keys, or evaluation artifacts.
- `.repoevidenceignore` only adds exclusions; it cannot override existing secret/symlink/path-safety checks.
- CI and `make ci` never invoke a hosted chat model or require an API key.
- Snapshot deletion is opt-in, previewed, and may never delete current, building, or session-referenced evidence.
- `make deliver` must refuse secrets, ignored local state, a dirty unexpected index, or an unconfigured `origin/main`.

---

### Task 1: Make local and GitHub quality gates identical

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `Makefile`
- Modify: `README.md`
- Test: `.github/workflows/ci.yml` through actionlint-compatible YAML assertions in `backend/tests/test_delivery_files.py`

**Interfaces:**
- Consumes: existing `make test`, `make frontend-test`, and `make frontend-build` commands.
- Produces: `make ci`, a no-network quality command with a non-zero exit at the first failed gate.

- [ ] **Step 1: Write failing delivery-file tests**

```python
def test_ci_workflow_invokes_the_single_make_target(repo_root: Path) -> None:
    workflow = (repo_root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "make ci" in workflow
    assert "pull_request:" in workflow

def test_makefile_declares_ci_without_delivery_side_effects(repo_root: Path) -> None:
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")
    assert "ci:" in makefile
    assert "eval-online" not in makefile.split("ci:", 1)[1].split("\n\n", 1)[0]
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_delivery_files.py -q`

Expected: FAIL because the workflow and `ci` target do not yet exist.

- [ ] **Step 3: Implement the shared CI target and workflow**

Add `ci` to `.PHONY`; make it run `quality` then `eval`. Create a GitHub Actions workflow triggered by `push` and `pull_request`, installing Python 3.12, uv, Node 22, backend dependencies, frontend dependencies, then `make ci`.

- [ ] **Step 4: Re-run focused tests and local CI**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_delivery_files.py -q && make ci`

Expected: PASS; no network model request is made.

- [ ] **Step 5: Commit**

```bash
git add Makefile README.md .github/workflows/ci.yml backend/tests/test_delivery_files.py
git commit -m "ci: add shared local and GitHub quality gate"
```

### Task 2: Add deterministic offline evaluation reports

**Files:**
- Create: `backend/src/repo_evidence/evaluation/runner.py`
- Create: `backend/src/repo_evidence/evaluation/contracts.py`
- Create: `backend/tests/evaluation/test_runner.py`
- Create: `evaluation/cases/repository-evidence.json`
- Modify: `Makefile`
- Modify: `.gitignore`
- Modify: `README.md`

**Interfaces:**
- Consumes: `repositories.evaluation.Citation`, `RepositoryCase`, and `score_case`.
- Produces: `run_offline_evaluation(case_file: Path, report_dir: Path) -> EvaluationReport` and `make eval`.

- [ ] **Step 1: Write failing runner tests**

```python
def test_runner_writes_redacted_summary_and_local_detail(tmp_path: Path) -> None:
    report = run_offline_evaluation(case_file, tmp_path)
    assert report.total == 5
    assert report.summary_path.name == "offline-evaluation.md"
    assert report.detail_path.name == "offline-evaluation.json"
    assert "API_KEY" not in report.summary_path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `backend/.venv/bin/python -m pytest backend/tests/evaluation/test_runner.py -q`

Expected: FAIL because no runner exists.

- [ ] **Step 3: Implement fixture validation, scoring, and report generation**

Load versioned cases with expected citations and a deterministic candidate answer/citations for offline assertions. Aggregate pass count, citation accuracy, coverage, duration, and failing IDs. Write detailed JSON only under ignored `artifacts/evaluation/`; write a summary Markdown report under `docs/quality/` without answers, keys, or host paths.

- [ ] **Step 4: Wire `make eval` and document online evaluation boundary**

Add `eval` to run the runner. Add `eval-online` as a manual-only target that fails early unless explicit opt-in and model configuration are present. Document that CI calls only `eval`.

- [ ] **Step 5: Re-run the focused suite and generated report check**

Run: `backend/.venv/bin/python -m pytest backend/tests/evaluation backend/tests/repositories/test_evaluation.py -q && make eval`

Expected: PASS and an ignored detailed artifact plus a safe Markdown summary.

- [ ] **Step 6: Commit**

```bash
git add backend/src/repo_evidence/evaluation backend/tests/evaluation evaluation/cases Makefile .gitignore README.md docs/quality
git commit -m "test: add deterministic repository evidence evaluation"
```

### Task 3: Add safe ignore rules and index progress

**Files:**
- Create: `backend/src/repo_evidence/repositories/progress.py`
- Modify: `backend/src/repo_evidence/repositories/scanner.py`
- Modify: `backend/src/repo_evidence/repositories/indexer.py`
- Modify: `backend/src/repo_evidence/bootstrap.py`
- Modify: `backend/src/repo_evidence/core/http/app.py`
- Modify: `backend/src/repo_evidence/repositories/models.py`
- Modify: `backend/tests/repositories/test_scanner.py`
- Modify: `backend/tests/repositories/test_indexer.py`
- Modify: `backend/tests/repositories/test_http_api.py`
- Modify: `frontend/src/state.ts`
- Modify: `frontend/src/lib/session-api.ts`
- Modify: `frontend/src/components/repository-panel.tsx`
- Modify: `frontend/src/hooks/use-repositories.test.tsx`

**Interfaces:**
- Consumes: `RepositoryScanner.scan(root)` and `RepositoryIndexer.build(repository_id)`.
- Produces: `IndexProgress` in every repository API view and optional `.repoevidenceignore` exclusions.

- [ ] **Step 1: Write failing scanner and API tests**

```python
def test_scanner_excludes_posix_globs_from_repoevidenceignore(tmp_path: Path) -> None:
    (tmp_path / ".repoevidenceignore").write_text("generated/\n*.log\n")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated/out.py").write_text("x = 1\n")
    (tmp_path / "debug.log").write_text("safe log\n")
    result = RepositoryScanner().scan(tmp_path)
    assert {item.path for item in result.files} == {".repoevidenceignore"}
    assert {item.reason for item in result.excluded} >= {"user ignore rule"}

def test_repository_view_exposes_index_progress(client: TestClient) -> None:
    assert client.get("/api/repositories/repo-1").json()["index_progress"]["phase"] == "scanning"
```

- [ ] **Step 2: Run focused tests and confirm failure**

Run: `backend/.venv/bin/python -m pytest backend/tests/repositories/test_scanner.py backend/tests/repositories/test_http_api.py -q`

Expected: FAIL because ignore rules and progress fields do not exist.

- [ ] **Step 3: Implement scanner-only additive rules**

Parse `.repoevidenceignore` safely as UTF-8, treat malformed/oversized files as excluded, and match only normalized repository-relative POSIX paths. Do not support `!` negation. Count rule exclusions without retaining their content.

- [ ] **Step 4: Implement in-process progress lifecycle**

Introduce a typed `IndexProgressStore` injected into the indexer/application. Update it at scan completion, corpus preparation, document reuse, each embedding batch, final verification, success, and failure. Preserve a terminal progress record for the panel while the process lives; do not make it a false durable job after restart.

- [ ] **Step 5: Return and render progress**

Extend `RepositoryRecord` serialization and TypeScript repository types. Continue existing 1.5-second polling while indexing. Render phase, scan/ignore/reuse/new-embed counts, elapsed time, and final reuse rate without exposing host paths.

- [ ] **Step 6: Run targeted backend and frontend tests**

Run: `backend/.venv/bin/python -m pytest backend/tests/repositories/test_scanner.py backend/tests/repositories/test_indexer.py backend/tests/repositories/test_http_api.py -q && npm --prefix frontend test -- --run src/hooks/use-repositories.test.tsx src/App.test.tsx`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/repo_evidence/repositories backend/src/repo_evidence/bootstrap.py backend/src/repo_evidence/core/http/app.py backend/tests/repositories frontend/src
git commit -m "feat: show repository index progress and ignore rules"
```

### Task 4: Add previewed, reference-safe snapshot cleanup

**Files:**
- Modify: `backend/src/repo_evidence/repositories/store.py`
- Modify: `backend/src/repo_evidence/core/knowledge/contracts.py`
- Modify: `backend/src/repo_evidence/core/knowledge/store.py`
- Modify: `backend/src/repo_evidence/core/event_log/store.py`
- Modify: `backend/src/repo_evidence/core/http/app.py`
- Modify: `backend/tests/repositories/test_store.py`
- Modify: `backend/tests/repositories/test_http_api.py`
- Modify: `frontend/src/lib/session-api.ts`
- Modify: `frontend/src/components/repository-panel.tsx`
- Modify: `frontend/src/components/repository-panel.test.tsx`

**Interfaces:**
- Consumes: snapshot metadata, `EventStore` session collection IDs, and vector-store collection deletion.
- Produces: `POST /api/repositories/{id}/snapshots/prune-preview` and `POST /api/repositories/{id}/snapshots/prune`.

- [ ] **Step 1: Write failing eligibility tests**

```python
def test_prune_preview_keeps_current_building_and_session_bound_snapshots(store, events) -> None:
    preview = store.preview_prune(repository_id, protected_collection_ids=events.collection_ids())
    assert preview.candidate_collection_ids == ("failed-v1",)
    assert "current-v3" not in preview.candidate_collection_ids
    assert "history-v2" not in preview.candidate_collection_ids
```

- [ ] **Step 2: Run focused tests and confirm failure**

Run: `backend/.venv/bin/python -m pytest backend/tests/repositories/test_store.py backend/tests/repositories/test_http_api.py -q`

Expected: FAIL because prune preview and deletion APIs do not exist.

- [ ] **Step 3: Implement protected-ID lookup and two-step pruning**

Read collection IDs only from `session.created` event payloads. Compute candidates from the selected repository’s `failed` and `superseded` snapshots after excluding current/building/session-referenced collections. Require the exact preview token or candidate ID set when confirming; re-evaluate eligibility inside one SQLite transaction before registry deletion.

- [ ] **Step 4: Implement vector collection deletion with failure-safe ordering**

Add the narrow `delete_collection(collection_id)` builder operation. Delete vector data first; delete registry/file-manifest rows only after it succeeds. Return a safe summary; if one collection delete fails, preserve its registry row and report the failure.

- [ ] **Step 5: Add a confirmation UI**

Add a panel action that requests preview, lists candidate collection IDs/status/timestamps, and requires the user to press a second destructive-labelled confirmation button. Refresh repository state after completion and display deleted/skipped counts.

- [ ] **Step 6: Run targeted tests**

Run: `backend/.venv/bin/python -m pytest backend/tests/repositories/test_store.py backend/tests/repositories/test_http_api.py -q && npm --prefix frontend test -- --run src/components/repository-panel.test.tsx`

Expected: PASS; tests prove protected history cannot be pruned.

- [ ] **Step 7: Commit**

```bash
git add backend/src/repo_evidence backend/tests/repositories frontend/src
git commit -m "feat: add safe repository snapshot cleanup"
```

### Task 5: Implement guarded GitHub initialization and delivery

**Files:**
- Create: `scripts/delivery/github_init.py`
- Create: `scripts/delivery/deliver.py`
- Create: `backend/tests/scripts/test_delivery.py`
- Modify: `Makefile`
- Modify: `.gitignore`
- Modify: `README.md`
- Modify: `docs/DEMO.md`

**Interfaces:**
- Consumes: `make ci`, Git, GitHub CLI, and a user-supplied confirmation phrase.
- Produces: `make github-init` and `make deliver MESSAGE="..."`.

- [ ] **Step 1: Write failing command-construction and safety tests**

```python
def test_deliver_refuses_env_and_artifact_paths(tmp_path: Path) -> None:
    result = inspect_release_paths([".env", "artifacts/evaluation/raw.json"])
    assert result.blocked == (".env", "artifacts/evaluation/raw.json")

def test_deliver_pushes_only_after_ci_and_explicit_confirmation(fake_runner) -> None:
    deliver(fake_runner, confirmation="PUSH")
    assert fake_runner.calls == ["make ci", "git add ...", "git commit ...", "git push origin main"]
```

- [ ] **Step 2: Run focused tests and confirm failure**

Run: `backend/.venv/bin/python -m pytest backend/tests/scripts/test_delivery.py -q`

Expected: FAIL because delivery scripts do not exist.

- [ ] **Step 3: Implement first-time private GitHub setup**

Use `gh auth status` and `gh repo create --private --source . --remote origin` only after an interactive confirmation. Refuse to overwrite an existing origin, refuse a non-GitHub remote, and explain how users can configure `git user.name`/`git user.email` without recording it in project files.

- [ ] **Step 4: Implement delivery preflight, report, commit, and push**

Run `make ci`, create a timestamped safe report, inspect Git paths before staging, block secrets/ignored artifacts, require a clean remote/upstream and explicit `PUSH` confirmation, stage only allowed source/docs/config files, commit with `MESSAGE` or a timestamped fallback, then push `origin main`.

- [ ] **Step 5: Re-run delivery tests and manual dry run**

Run: `backend/.venv/bin/python -m pytest backend/tests/scripts/test_delivery.py -q && make deliver DRY_RUN=1`

Expected: PASS; dry run prints intended actions and neither commits nor pushes.

- [ ] **Step 6: Run the full quality gate**

Run: `make ci`

Expected: all backend/frontend/offline-evaluation gates PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/delivery backend/tests/scripts Makefile .gitignore README.md docs/DEMO.md
git commit -m "feat: add guarded GitHub delivery workflow"
```

## Plan Self-Review

- Spec coverage: Task 1 covers shared CI; Task 2 covers developer-only deterministic and manual-online evaluation; Task 3 covers ignore rules and index progress; Task 4 covers reference-safe cleanup; Task 5 covers private GitHub initialization and guarded delivery.
- Placeholders: no implementation task delegates an undefined component; each production area has explicit tests and verification commands.
- Type consistency: HTTP repository views remain the browser boundary; progress and prune summaries are serializable API contracts; evaluation runners do not depend on hosted models.
