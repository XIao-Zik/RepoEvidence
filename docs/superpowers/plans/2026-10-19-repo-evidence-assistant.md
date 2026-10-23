# RepoEvidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the FlowPay teaching application with a single-repository, local-first code evidence Q&A and read-only troubleshooting product suitable for an interview demonstration.

**Architecture:** Keep the typed LLM, pgvector, event log, session manager, and SSE foundations. Replace FlowPay data ingestion, stage routing, and payment tools with a repository registry, immutable repository snapshot collections, a safe scanner/chunker, a unified evidence agent, and repository/index UI. Every current answer is bound to one ready collection; source changes make that collection stale until a user-approved refresh publishes a successor.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, PostgreSQL 17 + pgvector, SQLAlchemy/Alembic, OpenAI-compatible APIs, Ollama, React, TypeScript, Vite, Vitest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-19-repo-evidence-assistant-design.md`

## 实施状态（2026-10-20）

本文件下面的复选框保留为最初的逐步实施记录；由于工作树尚未配置 Git 提交身份，计划中的 `git commit` 步骤没有执行。功能实现与验证以此状态表为准：

| 任务 | 状态 | 说明 |
| --- | --- | --- |
| 1–11 | 已实现 | 产品已改名为 RepoEvidence；仓库登记、安全扫描、行级分块、不可变索引、只读证据 Agent（代码、文档、日志检索及目录列表）、会话绑定、HTTP API 与仓库选择 UI 均已落地。刷新会为代码变更发布独立快照，并复用未变文件的已存文档、分块与向量。 |
| 12 | 已实现 | 引用显示为 `path:start-end`；点击后请求冻结证据 API 并以带行号的文本查看。旧会话显式携带 collection ID，可回看 superseded 快照。 |
| 13 | 已实现 | 使用 `repositories/evaluation.py` 的确定性证据评分器替换原支付场景基准，避免依赖真实模型调用。 |
| 14 | 已实现，存在环境验证待办 | 项目指南、演示脚本与文档验收测试已完成。后端 121 通过（4 项 PostgreSQL 选择性跳过）、前端 55 通过、生产构建、Ruff、basedpyright（0 error）和 Compose 配置均已验证。真实 PostgreSQL 的 4 项测试已验证快照刷新能复用未变文件向量且不混淆新旧集合；真实 Docker 演示仍需要你的模型密钥和一个允许的本地仓库。 |

已知质量待办：`basedpyright` 已无 error，但严格建议模式仍报告 SQLite 行类型、测试 mock 和未标注类属性的警告；前端全库 Prettier 也有本次变更以外的历史格式债务。它们不影响已通过的运行时测试与生产构建，但在首次正式提交前应单独治理，避免将大规模格式化与功能改造混在同一个提交中。

## Global Constraints

- Rename all product-facing FlowPay/teaching/s1--s4 terminology to RepoEvidence; rename the Python package from `flowpay` to `repo_evidence`.
- Support only a user-selected single repository at a time; do not add remote sync, mutation tools, arbitrary command execution, OCR, authentication, or multi-tenant behavior.
- Only resolve files below roots named by `REPO_EVIDENCE_ALLOWED_ROOTS`; never index, return, or send excluded file contents to a model.
- Collections are immutable after `ready`; refresh creates a new collection and must never silently overwrite evidence.
- Citations use normalized repository-relative POSIX paths and inclusive 1-based `start_line` / `end_line` values.
- Do not display model chain-of-thought. The UI may show tool name, parameter summary, result summary, final answer, and citation.
- All modifications must be covered by focused tests before the implementation test becomes green; run repository quality checks before completion.

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `backend/src/repo_evidence/core/` | Renamed generic runtime: LLM, vector store, session/event/SSE and typed tool protocol. |
| `backend/src/repo_evidence/repositories/models.py` | Repository, snapshot, file manifest, scan outcome and citation value objects. |
| `backend/src/repo_evidence/repositories/scanner.py` | Allowed-root path normalization, safe file walk, sensitive-file policy and SHA-256 manifest. |
| `backend/src/repo_evidence/repositories/chunking.py` | Line-preserving source/Markdown/log chunk production. |
| `backend/src/repo_evidence/repositories/store.py` | Local SQLite repository registry and current/stale/superseded snapshot persistence. |
| `backend/src/repo_evidence/repositories/indexer.py` | Build/verify/publish immutable repository collections and incremental reuse. |
| `backend/src/repo_evidence/repositories/tools.py` | Bounded search/read/list repository tool schemas and implementations. |
| `backend/src/repo_evidence/workflows/evidence.py` | One Evidence Agent loop and evidence-only prompt/answer contract. |
| `backend/src/repo_evidence/bootstrap.py` | Product dependency assembly without stage routing. |
| `backend/src/repo_evidence/core/http/app.py` | Existing session API plus repository/index/evidence endpoints. |
| `backend/tests/repositories/` | Scanner, chunks, persistence, index and tool contract tests. |
| `backend/tests/workflows/test_evidence.py` | Agent budget, evidence citation, stale-context and refusal tests. |
| `frontend/src/components/repository-panel.tsx` | Root/repository selection, index state and refresh controls. |
| `frontend/src/components/evidence-dialog.tsx` | Citation badges and frozen source fragment dialog. |
| `frontend/src/lib/session-api.ts` | Typed session and repository HTTP client. |
| `frontend/src/hooks/use-repositories.ts` | Repository state and scan/index operations. |
| `frontend/src/state.ts` | Product metadata, repository state and safe trace projections. |
| `docs/PROJECT_GUIDE.md` | Maintainer-facing product map and safe modification guide. |
| `docs/DEMO.md` | Interview demonstration script. |

## Task 1: Establish a Rename-Safe Product Baseline

**Files:**
- Modify: `backend/pyproject.toml`, `backend/src/flowpay/`, `backend/tests/`, `frontend/`, `README.md`, `.env.example`, `compose.yaml`, `Makefile`
- Create: `backend/tests/test_product_identity.py`

**Interfaces:**
- Produces importable package `repo_evidence`, console command `repo-evidence`, and matching container entry point.
- Preserves workflow selection temporarily; Task 7 removes it only after the unified Evidence Agent exists.

- [ ] **Step 1: Write failing identity tests.**

```python
def test_product_package_and_console_script_are_repo_evidence() -> None:
    metadata = tomllib.loads((ROOT / "backend/pyproject.toml").read_text())
    assert metadata["project"]["name"] == "repo-evidence"
    assert metadata["project"]["scripts"] == {"repo-evidence": "repo_evidence.cli:main"}
    assert (ROOT / "backend/src/repo_evidence/__init__.py").is_file()
```

- [ ] **Step 2: Run the identity test and verify it fails.**

Run: `cd backend && uv run pytest tests/test_product_identity.py -q`

Expected: FAIL because the source directory and package metadata still name FlowPay.

- [ ] **Step 3: Rename the source package and runtime entry point.**

Move `backend/src/flowpay` to `backend/src/repo_evidence`, update all Python imports, package metadata, CLI script and Docker application command. Retain stage configuration until Task 7 replaces the workflows, so every intermediate revision remains executable. Do not change core behavior in this task.

- [ ] **Step 4: Run identity and import smoke tests.**

Run: `cd backend && uv run pytest tests/test_product_identity.py tests/core/test_config_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add backend frontend README.md .env.example compose.yaml Makefile
git commit -m "refactor: rename product to repo evidence"
```

## Task 2: Define Repository Configuration and Registry Persistence

**Files:**
- Create: `backend/src/repo_evidence/repositories/__init__.py`, `models.py`, `store.py`
- Modify: `backend/src/repo_evidence/core/config/settings.py`
- Test: `backend/tests/repositories/test_store.py`, `backend/tests/core/test_config_api.py`

**Interfaces:**
- `RepositoryRecord(id: str, root_id: str, relative_path: str, display_name: str, current_collection_id: str | None, status: Literal["unindexed", "indexing", "ready", "stale", "failed"])`
- `RepositorySnapshot(repository_id: str, collection_id: str, manifest_sha256: str, parent_collection_id: str | None, status: Literal["building", "ready", "failed", "superseded"], created_at: datetime)`
- `RepositoryStore.register(root_id: str, relative_path: str) -> RepositoryRecord`
- `RepositoryStore.activate_snapshot(repository_id: str, snapshot_id: str) -> RepositoryRecord`

- [ ] **Step 1: Write failing store/config tests.**

```python
def test_registers_only_a_normalized_relative_repository_path(store: RepositoryStore) -> None:
    record = store.register("projects", "sample-api")
    assert record.relative_path == "sample-api"
    assert record.status == "unindexed"

def test_settings_rejects_an_empty_allowed_root_list() -> None:
    with pytest.raises(ValueError, match="allowed repository root"):
        load_settings_from_mapping({"repository_roots": []})
```

- [ ] **Step 2: Run repository store tests to verify failure.**

Run: `cd backend && uv run pytest tests/repositories/test_store.py tests/core/test_config_api.py -q`

Expected: FAIL with missing repository models/store/settings.

- [ ] **Step 3: Add settings, migrations and a typed repository store.**

Add `RepositoryRootSettings` parsed from `REPO_EVIDENCE_ALLOWED_ROOTS` and make empty roots invalid for normal startup. Add `repositories` and `repository_snapshots` tables to the product-local SQLite registry; use transactionally guarded transitions so only a ready snapshot can become current. Keep pgvector roles limited to collection/document/chunk access. Persist no host absolute path in collection metadata; persist root ID and root-relative path.

- [ ] **Step 4: Run migrations and focused tests.**

Run: `cd backend && uv run pytest tests/repositories/test_store.py tests/core/test_config_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add backend/src/repo_evidence/repositories backend/src/repo_evidence/core/config backend/tests
git commit -m "feat: add repository registry and snapshots"
```

## Task 3: Implement Safe Repository Scanning and Manifests

**Files:**
- Create: `backend/src/repo_evidence/repositories/policy.py`, `scanner.py`
- Test: `backend/tests/repositories/test_policy.py`, `test_scanner.py`, fixtures in `backend/tests/repositories/fixtures/sample_repo/`

**Interfaces:**
- `ScanPolicy(max_file_bytes: int, excluded_directories: frozenset[str], excluded_names: frozenset[str])`
- `RepositoryScanner.scan(root: Path) -> ScanResult`
- `ScanResult(manifest: tuple[ScannedFile, ...], manifest_sha256: str, excluded: tuple[ExcludedFile, ...])`
- `resolve_under_root(root: Path, relative_path: str) -> Path`

- [ ] **Step 1: Write failing traversal and exclusion tests.**

```python
def test_scan_excludes_env_secrets_dependencies_and_binary_files(sample_repo: Path) -> None:
    result = RepositoryScanner(DEFAULT_POLICY).scan(sample_repo)
    assert {item.path for item in result.manifest} == {"README.md", "src/auth.py"}
    assert {item.path for item in result.excluded} >= {".env", "node_modules/x.js", "keys/server.pem"}

def test_resolve_under_root_rejects_escape_and_symlink_escape(sample_repo: Path) -> None:
    with pytest.raises(RepositoryPathError):
        resolve_under_root(sample_repo, "../outside.txt")
```

- [ ] **Step 2: Run the scanner tests and verify failure.**

Run: `cd backend && uv run pytest tests/repositories/test_policy.py tests/repositories/test_scanner.py -q`

Expected: FAIL because scanner/policy are not implemented.

- [ ] **Step 3: Implement deterministic, safe scanning.**

Resolve the root once; reject non-directories. Use `Path.rglob`/`os.scandir` with no symlink traversal, normalize each candidate to POSIX relative path, apply directory/name/pattern/size rules before reading bytes, then reject binary/NUL content and high-risk secret names/content. Decode UTF-8 text with explicit failure exclusion, calculate SHA-256, line count and language/content type. Hash sorted metadata to derive `manifest_sha256`.

- [ ] **Step 4: Run focused scanner tests.**

Run: `cd backend && uv run pytest tests/repositories/test_policy.py tests/repositories/test_scanner.py -q`

Expected: PASS, including the symlink and traversal cases.

- [ ] **Step 5: Commit.**

```bash
git add backend/src/repo_evidence/repositories backend/tests/repositories
git commit -m "feat: safely scan authorized repositories"
```

## Task 4: Produce Line-Accurate Repository Chunks

**Files:**
- Create: `backend/src/repo_evidence/repositories/chunking.py`
- Modify: `backend/src/repo_evidence/core/knowledge/models.py`, `contracts.py`
- Test: `backend/tests/repositories/test_chunking.py`

**Interfaces:**
- `ChunkLocator(path: str, start_line: int, end_line: int, language: str, content_type: str, symbol: str | None)`
- `chunk_file(file: ScannedFile, max_lines: int) -> list[PendingChunk]`

- [ ] **Step 1: Write failing line-location tests.**

```python
def test_python_function_chunk_preserves_exact_inclusive_line_range() -> None:
    chunks = chunk_file(scanned("src/auth.py", "def login():\n    return True\n"), 80)
    assert chunks[0].source.locator == ChunkLocator("src/auth.py", 1, 2, "python", "source", "login")

def test_markdown_chunks_on_heading_and_never_drop_a_line() -> None:
    chunks = chunk_file(scanned("README.md", "# Install\ntext\n# Run\ntext\n"), 80)
    assert [(c.source.locator.start_line, c.source.locator.end_line) for c in chunks] == [(1, 2), (3, 4)]
```

- [ ] **Step 2: Run chunking tests and verify failure.**

Run: `cd backend && uv run pytest tests/repositories/test_chunking.py -q`

Expected: FAIL because `ChunkLocator` and `chunk_file` do not exist.

- [ ] **Step 3: Implement deterministic language-aware chunkers.**

Use the standard library AST for Python module/class/function boundaries. For TypeScript, Go, Java, JSON/YAML/TOML and unknown text, use a line window with overlap and nearest declaration/header metadata; do not claim semantic AST parsing for unsupported languages. Split Markdown by heading and paragraphs; split logs by timestamp/error-context boundaries. Every chunk must retain original line endings and inclusive source line coordinates.

- [ ] **Step 4: Run chunking and knowledge serialization tests.**

Run: `cd backend && uv run pytest tests/repositories/test_chunking.py tests/core/test_knowledge.py -q`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add backend/src/repo_evidence/repositories/chunking.py backend/src/repo_evidence/core/knowledge backend/tests/repositories/test_chunking.py
git commit -m "feat: add line-addressable repository chunks"
```

## Task 5: Build Immutable Repository Collections and Refreshes

**Files:**
- Create: `backend/src/repo_evidence/repositories/indexer.py`, `scripts/repository_index.py`
- Modify: `backend/src/repo_evidence/core/knowledge/store.py`, `client.py`, `scripts/database.py`
- Test: `backend/tests/repositories/test_indexer.py`, `backend/tests/repositories/test_collection_versions.py`

**Interfaces:**
- `RepositoryIndexer.plan(record: RepositoryRecord) -> IndexPlan`
- `RepositoryIndexer.build(repository_id: str, expected_manifest_sha256: str) -> RepositorySnapshot`
- `RepositoryIndexer.detect_changes(repository_id: str) -> ChangeSummary`
- `IndexPlan(collection_id: str, manifest_sha256: str, added: int, changed: int, deleted: int, reused: int)`
- `ChangeSummary(status: Literal["unchanged", "stale"], added: tuple[str, ...], changed: tuple[str, ...], deleted: tuple[str, ...])`

- [ ] **Step 1: Write failing immutable-version tests.**

```python
async def test_refresh_publishes_new_collection_and_keeps_old_evidence(indexer, repository) -> None:
    first = await indexer.build(repository.id, indexer.plan(repository).manifest_sha256)
    (repository.root / "src/auth.py").write_text("def login():\n    return False\n")
    second = await indexer.build(repository.id, indexer.plan(repository).manifest_sha256)
    assert second.collection_id != first.collection_id
    assert read_passage(first.collection_id, "src/auth.py").text.endswith("True\n")
    assert read_passage(second.collection_id, "src/auth.py").text.endswith("False\n")
```

- [ ] **Step 2: Run indexer tests and verify failure.**

Run: `cd backend && uv run pytest tests/repositories/test_indexer.py tests/repositories/test_collection_versions.py -q`

Expected: FAIL because repository collection planner/indexer is absent.

- [ ] **Step 3: Implement plan/build/verify/publish lifecycle.**

Derive collection IDs from stable repository ID, manifest hash, indexer strategy version and embedding identity. Insert a `building` collection, save documents/chunks, embed only content hashes not already reusable from the parent ready collection, re-scan/re-hash files before publish, verify every locator round-trips to its frozen document, mark ready, then transactionally activate the snapshot. Any exception marks only the new snapshot failed and leaves current ready snapshot unchanged.

Implement `detect_changes` from the same scanner manifest: compare the current work-tree against the active snapshot and persist `stale` plus the added/changed/deleted summary. A stale scan never mutates the active collection. Before a question and immediately before bounded file reads, re-check the manifest; if it changed in either window, reject the operation with a refresh-required error rather than combining current bytes with old embeddings.

- [ ] **Step 4: Add and run index CLI verification.**

Run: `cd backend && uv run pytest tests/repositories/test_indexer.py tests/repositories/test_collection_versions.py -q`

Expected: PASS; test output proves both collections remain readable.

- [ ] **Step 5: Commit.**

```bash
git add backend/src/repo_evidence/repositories scripts/repository_index.py backend/src/repo_evidence/core/knowledge backend/tests/repositories scripts/database.py
git commit -m "feat: publish immutable repository indexes"
```

## Task 6: Add Bounded Read-Only Repository Tools

**Files:**
- Create: `backend/src/repo_evidence/repositories/tools.py`
- Modify: `backend/src/repo_evidence/core/tools/contracts.py`, `toolbox.py`
- Test: `backend/tests/repositories/test_tools.py`

**Interfaces:**
- `search_repository(query: str, path_glob: str | None = None, limit: int = 8) -> RepositorySearchResult`
- `read_file(path: str, start_line: int, end_line: int) -> FileReadResult`
- `search_docs(query: str, limit: int = 8) -> RepositorySearchResult`
- `search_logs(query: str, service: str, start: datetime | None, end: datetime | None) -> RepositorySearchResult`
- `list_directory(path: str = ".", depth: int = 2) -> DirectoryResult`
- `preview_patch(evidence: list[Citation], requested_changes: str) -> PatchPreview`

- [ ] **Step 1: Write failing tool boundary tests.**

```python
async def test_read_file_caps_lines_and_returns_source_hash(tools: RepositoryTools) -> None:
    result = await tools.read_file("src/auth.py", 1, 999)
    assert result.end_line <= 200
    assert result.source_sha256

async def test_tools_reject_sensitive_and_outside_paths(tools: RepositoryTools) -> None:
    with pytest.raises(ToolInputError, match="not readable"):
        await tools.read_file(".env", 1, 2)
```

- [ ] **Step 2: Run tool tests and verify failure.**

Run: `cd backend && uv run pytest tests/repositories/test_tools.py -q`

Expected: FAIL with missing repository tools.

- [ ] **Step 3: Implement schemas and tool adapters.**

Search only the session's ready collection. Validate `path_glob`, all relative paths, limit and read window before opening a file; reject stale/current-file hash mismatch as a reindex requirement rather than returning mixed-version text. Keep full document reads unavailable. Return compact source metadata and exact lines, and emit structured tool events through the existing runtime.

Do not give the agent a write/apply/command/Git tool. If a repair is requested, it may produce a `PatchPreview` containing cited source locations, assumptions, a unified diff and its target relative paths. Validate every target is within the authorized repository, then return preview-only content or a downloadable `.patch`; never modify the source tree. Any user-applied patch is detected through the normal stale check.

- [ ] **Step 4: Run tool tests.**

Run: `cd backend && uv run pytest tests/repositories/test_tools.py tests/core/test_typed_boundaries.py -q`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add backend/src/repo_evidence/repositories/tools.py backend/src/repo_evidence/core/tools backend/tests/repositories/test_tools.py
git commit -m "feat: add safe repository investigation tools"
```

## Task 7: Replace Teaching Workflows with One Evidence Agent

**Files:**
- Create: `backend/src/repo_evidence/workflows/evidence.py`
- Delete: `backend/src/repo_evidence/router.py`, `backend/src/repo_evidence/workflows/s1.py`, `s2.py`, `s3.py`, `s4.py`
- Modify: `backend/src/repo_evidence/bootstrap.py`, `cli.py`, `core/agent/reply.py`
- Test: `backend/tests/workflows/test_evidence.py`, update `backend/tests/workflows/test_agent_loop.py`

**Interfaces:**
- `EvidenceWorkflow(model: ChatModel, tools: RepositoryTools, query_formatter: Callable[[str], str])`
- `EvidenceWorkflow.id == "repo-evidence-agent"`
- answer payload contains `conclusion`, `evidence`, `eliminated`, `next_steps`, `uncertainty`.

- [ ] **Step 1: Write failing unified workflow tests.**

```python
async def test_agent_rejects_a_claim_without_current_collection_evidence(workflow, turn, runtime) -> None:
    runtime.model.reply("认证在 auth.py。")
    with pytest.raises(ModelOutputError, match="citation"):
        await workflow.run(turn, runtime)

async def test_agent_uses_search_then_bounded_read_before_answer(workflow, turn, runtime) -> None:
    result = await workflow.run(turn, runtime)
    assert result.state["evidence"][0]["path"] == "src/auth.py"
```

- [ ] **Step 2: Run workflow tests and verify failure.**

Run: `cd backend && uv run pytest tests/workflows/test_evidence.py -q`

Expected: FAIL because the unified evidence workflow is not present.

- [ ] **Step 3: Implement the evidence-only agent loop.**

Reuse `AgentTurn`, budget, schema validation and event tracing. Offer search/read/docs/log/list tools under one budget and deduplication key. Replace the prompt with the required conclusion/evidence/eliminated/next steps/uncertainty format. Validate citations against current collection locator records before `accept`; send concise validation feedback to the model rather than accepting unsupported text. Bootstrap exactly this workflow; remove stage command-line and configuration behavior.

- [ ] **Step 4: Run workflow and generic Agent tests.**

Run: `cd backend && uv run pytest tests/workflows/test_evidence.py tests/workflows/test_agent_loop.py -q`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add backend/src/repo_evidence/workflows backend/src/repo_evidence/bootstrap.py backend/src/repo_evidence/cli.py backend/tests/workflows
git commit -m "feat: replace stages with evidence agent"
```

## Task 8: Bind Sessions to Repository Evidence Context

**Files:**
- Create: `backend/src/repo_evidence/repositories/context.py`
- Modify: `backend/src/repo_evidence/core/chat/context.py`, `types.py`, `core/execution/manager.py`, `workflows/evidence.py`
- Test: `backend/tests/repositories/test_context.py`, `backend/tests/workflows/test_evidence.py`

**Interfaces:**
- `InvestigationContext(repository_id: str, collection_id: str, service: str | None, environment: str | None, error_code: str | None, trace_id: str | None, start: datetime | None, end: datetime | None)`
- `invalidate_for_collection_change(context: InvestigationContext, collection_id: str) -> InvestigationContext`

- [ ] **Step 1: Write failing context invalidation tests.**

```python
def test_collection_change_clears_old_evidence_but_keeps_user_scope() -> None:
    changed = invalidate_for_collection_change(context("repo-a", "old", service="api", environment="test"), "new")
    assert (changed.collection_id, changed.service, changed.environment) == ("new", "api", "test")
    assert changed.read_evidence == ()
```

- [ ] **Step 2: Run context tests and verify failure.**

Run: `cd backend && uv run pytest tests/repositories/test_context.py -q`

Expected: FAIL because investigation context is absent.

- [ ] **Step 3: Persist context and invalidate stale evidence.**

Store typed context inside existing session state at the JSON boundary. On repository switch, collection activation or stale detection, clear cached retrieval/read evidence and attach an explicit stale event to the session. Preserve user-provided service/environment/error context only when the repository stays the same. Never insert historical citations into a new collection prompt.

- [ ] **Step 4: Run context/session tests.**

Run: `cd backend && uv run pytest tests/repositories/test_context.py tests/core/test_runtime.py -q`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add backend/src/repo_evidence/repositories/context.py backend/src/repo_evidence/core/chat backend/src/repo_evidence/core/execution backend/tests
git commit -m "feat: scope sessions to repository evidence versions"
```

## Task 9: Expose Repository and Evidence HTTP APIs

**Files:**
- Modify: `backend/src/repo_evidence/core/http/app.py`, `bootstrap.py`, `core/http/__init__.py`
- Create: `backend/tests/repositories/test_http_api.py`

**Interfaces:**
- `GET /api/repositories/roots`
- `GET,POST /api/repositories`
- `GET /api/repositories/{id}`
- `POST /api/repositories/{id}/scan`
- `POST /api/repositories/{id}/indexes`
- `GET /api/repositories/{id}/evidence/{passage_id}`
- `POST /api/repositories/{id}/patch-preview`

- [ ] **Step 1: Write failing HTTP contract tests.**

```python
async def test_scan_marks_changed_repository_stale(client, repository_id: str) -> None:
    response = await client.post(f"/api/repositories/{repository_id}/scan")
    assert response.status_code == 200
    assert response.json()["status"] == "stale"

async def test_evidence_endpoint_returns_frozen_lines(client, repository_id: str, passage_id: str) -> None:
    response = await client.get(f"/api/repositories/{repository_id}/evidence/{passage_id}")
    assert response.json()["start_line"] <= response.json()["end_line"]
```

- [ ] **Step 2: Run HTTP tests and verify failure.**

Run: `cd backend && uv run pytest tests/repositories/test_http_api.py -q`

Expected: FAIL with 404 endpoints.

- [ ] **Step 3: Implement typed request/response models and routes.**

Resolve roots server-side; accept only root ID plus root-relative path. Run index builds as session-managed background work and emit `repository.index.*` events. A scan returns its added/changed/deleted summary and sets `stale` without overwriting the active collection. Require a repository/ready, non-stale collection before accepting a current-code user message. Add a patch-preview endpoint that validates citations and only returns/export a diff; it must never write repository files. Keep existing session/SSE/cancel/export APIs and map validation exceptions to 4xx responses without leaking host filesystem paths.

- [ ] **Step 4: Run HTTP and existing runtime API tests.**

Run: `cd backend && uv run pytest tests/repositories/test_http_api.py tests/core/test_config_api.py tests/core/test_session_delete.py -q`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add backend/src/repo_evidence/core/http backend/src/repo_evidence/bootstrap.py backend/tests/repositories/test_http_api.py
git commit -m "feat: expose repository index and evidence APIs"
```

## Task 10: Replace Dataset Startup with Authorized Root Startup

**Files:**
- Modify: `scripts/start.py`, `scripts/development/environment.py`, `scripts/doctor.py`, `scripts/knowledge.py`, `compose.yaml`, `.env.example`, `Makefile`, `backend/configs/local.toml`, `README.md`
- Delete: FlowPay dataset restore/simulator-specific scripts and configuration only after replacement tests pass
- Test: `backend/tests/scripts/test_start.py`, `test_environment.py`, `test_checks.py`

**Interfaces:**
- `.env` requires `REPO_EVIDENCE_MODEL_API_KEY` and `REPO_EVIDENCE_ALLOWED_ROOTS`.
- Compose mounts each authorized root read-only below `/repositories/<root-id>`.
- `make up` starts database, Ollama, backend and frontend without a data bundle or prebuilt collection.

- [ ] **Step 1: Write failing startup tests.**

```python
def test_start_rejects_missing_allowed_repository_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPO_EVIDENCE_ALLOWED_ROOTS", raising=False)
    with pytest.raises(SystemExit, match="ALLOWED_ROOTS"):
        read_env()
```

- [ ] **Step 2: Run startup tests and verify failure.**

Run: `cd backend && uv run pytest tests/scripts/test_start.py tests/scripts/test_environment.py -q`

Expected: FAIL because startup still requires FlowPay dataset fields.

- [ ] **Step 3: Implement root-based local startup.**

Remove dataset archive extraction, simulator startup, fixed collection restore and stage environment variables. Parse the configured host paths before Compose, map them deterministically to container root IDs, mount read-only, validate the Ollama model for newly-built embeddings, and retain database migration/bootstrap behavior. Update Linux/WSL2 instructions and command names.

- [ ] **Step 4: Run startup tests and Docker configuration validation.**

Run: `cd backend && uv run pytest tests/scripts/test_start.py tests/scripts/test_environment.py tests/scripts/test_checks.py -q && cd .. && docker compose config >/dev/null`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add scripts compose.yaml .env.example Makefile backend/configs README.md backend/tests/scripts
git commit -m "feat: start repo evidence without a dataset bundle"
```

## Task 11: Build Repository Selection and Index Status UI

**Files:**
- Create: `frontend/src/lib/repository-api.ts`, `frontend/src/hooks/use-repository.ts`, `frontend/src/components/repository-panel.tsx`, corresponding `*.test.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/state.ts`, `frontend/src/lib/session-api.ts`

**Interfaces:**
- `RepositoryView` mirrors API status, snapshot, counts and stale change summary.
- `useRepository()` exposes `select`, `scan`, `createIndex`, `refresh`, `loading`, `error`.

- [ ] **Step 1: Write failing component tests.**

```tsx
it('shows a stale warning and only enables refresh after changes are detected', async () => {
  render(<RepositoryPanel repository={staleRepository} onRefresh={refresh} />)
  expect(screen.getByText('索引已过期')).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: '刷新索引' }))
  expect(refresh).toHaveBeenCalledWith(staleRepository.id)
})
```

- [ ] **Step 2: Run frontend test and verify failure.**

Run: `npm --prefix frontend test -- repository-panel.test.tsx`

Expected: FAIL because repository UI does not exist.

- [ ] **Step 3: Implement root/repository controls.**

Add a compact repository panel above chat: root selector, relative repository choice, index/create/scan/refresh action, snapshot status and file/chunk counts. Disable chat submission when no ready current collection exists. Render backend validation failures as plain user-facing messages.

- [ ] **Step 4: Run focused frontend tests.**

Run: `npm --prefix frontend test -- repository-panel.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add frontend/src
git commit -m "feat: add repository index controls"
```

## Task 12: Render Evidence and Safe Investigation Trace

**Files:**
- Create: `frontend/src/components/evidence-viewer.tsx`, tests
- Modify: `frontend/src/event-data.ts`, `state.ts`, `components/chat-panel.tsx`, `components/run-trace.tsx`, `components/trace-message.tsx`

**Interfaces:**
- `EvidenceView` adds `path`, `startLine`, `endLine`, `collectionId`, `sourceSha256`, `stale`.
- Evidence click fetches frozen source through the API; it does not ask the browser to read a host file.

- [ ] **Step 1: Write failing evidence renderer tests.**

```tsx
it('renders an exact source citation and opens frozen source text', async () => {
  render(<EvidenceViewer evidence={evidence('src/auth.py', 12, 24)} />)
  await userEvent.click(screen.getByRole('button', { name: 'src/auth.py:12-24' }))
  expect(await screen.findByText('def authenticate')).toBeVisible()
})
```

- [ ] **Step 2: Run renderer test and verify failure.**

Run: `npm --prefix frontend test -- evidence-viewer.test.tsx`

Expected: FAIL because citations are document-oriented and no evidence viewer exists.

- [ ] **Step 3: Implement citation and trace projections.**

Project repository search/read/index events into short labels and result counts; explicitly omit `model.chunk`, raw system prompt and internal model explanation from the visible trace. Render citations as `path:start-end`, show collection/version and stale status, and load source through `GET evidence` into a dialog with line numbers.

- [ ] **Step 4: Run frontend evidence tests.**

Run: `npm --prefix frontend test -- evidence-viewer.test.tsx event-data.test.ts state.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add frontend/src
git commit -m "feat: show versioned code evidence"
```

## Task 13: Replace Benchmarks with Evidence Fidelity Evaluation

**Files:**
- Create: `scripts/benchmark/repository_cases.py`, `backend/tests/repositories/test_evaluation.py`, fixture repositories
- Modify: `scripts/benchmark/run.py`, `scripts/benchmark/render.py`, `scripts/check_quality.py`

**Interfaces:**
- `RepositoryCase(question, repository_fixture, expected_paths, expected_line_ranges, required_terms, forbidden_paths)`
- `score_case(answer, cited_passages, case) -> EvidenceScore`

- [ ] **Step 1: Write failing evaluator tests.**

```python
def test_score_rejects_a_correct_sounding_answer_with_wrong_file_evidence() -> None:
    score = score_case("认证在这里", [citation("README.md", 1, 2)], auth_case())
    assert score.citation_accuracy == 0
    assert not score.passed
```

- [ ] **Step 2: Run evaluator tests and verify failure.**

Run: `cd backend && uv run pytest tests/repositories/test_evaluation.py -q`

Expected: FAIL because repository benchmark contracts are absent.

- [ ] **Step 3: Implement small, deterministic fixture benchmark.**

Replace payment questions with code location, configuration flow and exception handling cases. Score retrieval hit, exact citation/path/line support, forbidden stale/wrong citations, tool duplication and model/tool counts. Keep LLM-dependent runs opt-in; unit tests use scripted model fixtures.

- [ ] **Step 4: Run evaluator tests.**

Run: `cd backend && uv run pytest tests/repositories/test_evaluation.py tests/scripts/test_benchmark_harness.py -q`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add scripts/benchmark backend/tests/repositories backend/tests/scripts/test_benchmark_harness.py
git commit -m "feat: evaluate repository evidence fidelity"
```

## Task 14: Write the Maintainer Guide, Demo, and Final Verification

**Files:**
- Create: `docs/PROJECT_GUIDE.md`, `docs/DEMO.md`
- Modify: `README.md`
- Test: backend and frontend full suites; Docker smoke test against a fixture repository

**Interfaces:**
- `PROJECT_GUIDE.md` contains a feature map, “where to change it” table, safe extension recipes and verification commands.
- `DEMO.md` contains setup, import/index/question/change/refresh demonstration steps and interview talking points.

- [ ] **Step 1: Write guide acceptance assertions.**

```python
def test_project_guide_links_every_public_product_module() -> None:
    guide = (ROOT / "docs/PROJECT_GUIDE.md").read_text()
    for heading in ("仓库索引", "证据问答", "变更检测", "前端", "我可以参与改什么"):
        assert heading in guide
```

- [ ] **Step 2: Run the guide test and verify failure.**

Run: `cd backend && uv run pytest tests/test_project_guide.py -q`

Expected: FAIL because the maintainer guide is absent.

- [ ] **Step 3: Write the documentation and a runnable demo.**

Document the architecture/data flow, every public feature, exact module ownership, low-risk changes (prompts, exclusions, chunking rules, UI), higher-risk changes (schema, policy, agent contract), and required tests. Provide a WSL2 setup plus an interview sequence: authorize fixture root, create index, ask a location question, edit a fixture file, observe stale state, refresh, and confirm a new citation version.

- [ ] **Step 4: Run full verification.**

Run:

```bash
cd backend && uv run ruff check src tests && uv run basedpyright && uv run pytest -q
npm --prefix ../frontend test -- --run
npm --prefix ../frontend run build
cd .. && docker compose config >/dev/null
```

Expected: all commands exit 0.

- [ ] **Step 5: Run Docker demonstration smoke test.**

Run: `REPO_EVIDENCE_ALLOWED_ROOTS="$PWD/backend/tests/repositories/fixtures" make up && make smoke`

Expected: API and frontend become healthy; fixture repository indexes; changed fixture file becomes stale and a refresh publishes a new collection.

- [ ] **Step 6: Commit.**

```bash
git add README.md docs backend/tests/test_project_guide.py
git commit -m "docs: add project guide and interview demo"
```
