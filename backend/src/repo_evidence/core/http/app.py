# 提供会话 HTTP 接口与 SSE 事件订阅；只推送已持久化事件，重连按序号补齐。
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncGenerator, Callable
from contextlib import ExitStack, asynccontextmanager
from typing import Annotated, ClassVar, Protocol, cast
from urllib.parse import unquote

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from repo_evidence.core.chat.errors import SessionNotFound
from repo_evidence.core.chat.types import SessionSummary, SessionView
from repo_evidence.core.config import Settings
from repo_evidence.core.event_log.runtime import open_store
from repo_evidence.core.event_log.types import Event
from repo_evidence.core.execution.contracts import Workflow
from repo_evidence.core.execution.errors import Conflict
from repo_evidence.core.execution.manager import SessionManager
from repo_evidence.core.execution.resources import RuntimeResources
from repo_evidence.core.execution.types import TurnRecord
from repo_evidence.core.knowledge.contracts import PassageLookup, VectorStore
from repo_evidence.repositories.patches import PatchCitation, validate_patch_preview
from repo_evidence.repositories.store import RepositoryPathError, RepositoryStore
from repo_evidence.repositories.tools import RepositoryTools

logger = logging.getLogger(__name__)


class MessageBody(BaseModel):
    """发送消息 HTTP 接口的请求体；校验幂等 ID 与正文长度并拒绝额外字段。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=20000)


class SessionCreateBody(BaseModel):
    """A new investigation may be unbound for generic workflows or bound to one ready snapshot."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    repository_id: str | None = Field(default=None, min_length=1, max_length=128)
    collection_id: str | None = Field(default=None, min_length=1, max_length=512)


class ContinueBody(BaseModel):
    """继续运行 HTTP 接口的请求体；只接收新的幂等 ID，原问题从会话记录读取。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)


class DeleteSessionsBody(BaseModel):
    """批量删除请求；限制数量并拒绝空白 ID，避免无界批量操作。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    session_ids: list[Annotated[str, Field(min_length=1, pattern=r"\S")]] = Field(
        min_length=1, max_length=100
    )


class DeleteSessionsResult(BaseModel):
    """返回实际删除的会话 ID；不存在的会话不影响幂等重试。"""

    deleted_ids: list[str]


class WorkflowInfo(BaseModel):
    """前端展示的工作流身份和说明。"""

    id: str
    version: str
    label: str
    description: str


class ApplicationInfo(BaseModel):
    """页面初始化所需的协议版本、工作流与输入提示。"""

    protocol_version: int = 1
    workflow: WorkflowInfo
    model: str
    examples: list[str]
    reply_hint: str | None


class RepositoryIndexer(Protocol):
    """The HTTP layer only needs to launch a safe, already-wired repository build."""

    async def build(self, repository_id: str) -> object: ...


class FrozenEvidenceReader(Protocol):
    """Read one immutable passage; this is intentionally narrower than full repository access."""

    def read_passage(self, collection_id: str, doc_id: str, chunk_no: int) -> PassageLookup: ...


class RepositoryRegistrationBody(BaseModel):
    """A repository is selected solely by an approved root ID and POSIX relative path."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    root_id: str = Field(min_length=1, max_length=128)
    relative_path: str = Field(min_length=1, max_length=4096)


class PatchCitationBody(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class PatchPreviewBody(BaseModel):
    """A suggested diff remains an inert review artifact at the HTTP boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=4000)
    diff: str = Field(min_length=1, max_length=100_000)
    citations: list[PatchCitationBody] = Field(min_length=1, max_length=32)


class SnapshotPruneBody(BaseModel):
    """The UI submits exactly the collection IDs it received from a fresh preview."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    collection_ids: list[str] = Field(min_length=1, max_length=100)


# 应用生命周期负责存储与任务恢复，HTTP 连接仅负责提交和读取。
def create_app(
    settings: Settings,
    workflow: Workflow,
    *,
    repository_store: RepositoryStore | None = None,
    repository_indexer: RepositoryIndexer | None = None,
    repository_evidence: FrozenEvidenceReader | None = None,
    repository_cleanup: Callable[[], None] | None = None,
) -> FastAPI:
    """构造与业务工作流解耦的 HTTP 应用。

    启动时获取存储锁并恢复中断状态，关闭时先结束管理器任务再释放存储。
    消息接口仅受理任务并返回 202；SSE 单独订阅持久化事件，不承载模型执行。
    网页断开连接只停止订阅，用户取消必须走 cancel 接口。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        with ExitStack() as stack:
            store = stack.enter_context(open_store(settings))
            resources = RuntimeResources.from_settings(settings)
            stack.callback(resources.close)
            manager = SessionManager(store, workflow, settings, resources)
            app.state.manager = manager
            # 先取得独占运行锁，再补齐遗留运行的中断状态，之后才开始接受请求。
            await manager.recover()
            try:
                yield
            finally:
                index_tasks = list(app.state.repository_index_tasks.values())
                for task in index_tasks:
                    task.cancel()
                await asyncio.gather(*index_tasks, return_exceptions=True)
                await manager.shutdown()
                if repository_cleanup is not None:
                    repository_cleanup()

    app = FastAPI(title="RepoEvidence", version="0.1.0", lifespan=lifespan)
    app.state.repository_store = repository_store
    app.state.repository_indexer = repository_indexer
    app.state.repository_evidence = repository_evidence
    app.state.repository_index_tasks = {}

    def get_manager() -> SessionManager:
        """生命周期已在 app.state 写入管理器；在框架的动态 State 边界恢复类型。"""
        return cast(SessionManager, app.state.manager)

    def repositories() -> RepositoryStore:
        """Return the registry only after administrator-approved roots have been configured."""
        store = cast(RepositoryStore | None, app.state.repository_store)
        if store is None:
            raise HTTPException(503, "Repository features are not configured")
        return store

    def indexer() -> RepositoryIndexer:
        """Index creation is exposed only when startup provided a configured builder."""
        value = cast(RepositoryIndexer | None, app.state.repository_indexer)
        if value is None:
            raise HTTPException(503, "Repository indexing is not configured")
        return value

    def frozen_evidence() -> FrozenEvidenceReader:
        """Use an injected test reader or the app's read-only vector connection."""
        injected = cast(FrozenEvidenceReader | None, app.state.repository_evidence)
        if injected is not None:
            return injected
        vectors = get_manager().resources.vectors
        if vectors is None:
            raise HTTPException(503, "Repository evidence is not configured")
        return cast(VectorStore, vectors)

    def repository_view(record: object) -> dict[str, object]:
        """Keep host paths private while returning enough state for the repository panel."""
        from repo_evidence.repositories.models import RepositoryRecord

        if not isinstance(record, RepositoryRecord):
            raise TypeError("Expected repository record")
        progress_store = getattr(app.state.repository_indexer, "progress", None)
        progress = progress_store.get(record.id) if progress_store is not None else None
        return {
            "id": record.id,
            "root_id": record.root_id,
            "relative_path": record.relative_path,
            "display_name": record.display_name,
            "status": record.status,
            "current_collection_id": record.current_collection_id,
            "index_progress": (
                {
                    "phase": progress.phase,
                    "scanned_files": progress.scanned_files,
                    "ignored_files": progress.ignored_files,
                    "reused_files": progress.reused_files,
                    "new_chunks": progress.new_chunks,
                    "elapsed_ms": progress.elapsed_ms,
                    "error": progress.error,
                }
                if progress is not None
                else None
            ),
        }

    async def not_found(_: Request, _error: Exception) -> JSONResponse:
        return JSONResponse({"detail": "Session not found"}, status_code=404)

    async def conflict(_: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=409)

    async def health() -> JSONResponse:
        healthy = not get_manager().storage_error
        return JSONResponse(
            {"status": "ok" if healthy else "storage_error"}, status_code=200 if healthy else 503
        )

    async def meta() -> ApplicationInfo:
        """返回有明确响应模式的页面初始化信息。"""
        return ApplicationInfo(
            workflow=WorkflowInfo(
                id=workflow.id,
                version=workflow.version,
                label=workflow.card.label,
                description=workflow.card.description,
            ),
            model=settings.model.name,
            examples=workflow.card.examples,
            reply_hint=workflow.card.reply_hint,
        )

    async def repository_roots() -> list[dict[str, str]]:
        return [{"id": root_id} for root_id in sorted(repositories().roots)]

    async def create_repository(body: RepositoryRegistrationBody) -> dict[str, object]:
        try:
            record = await asyncio.to_thread(
                repositories().register, body.root_id, body.relative_path
            )
        except RepositoryPathError as error:
            raise HTTPException(422, str(error)) from error
        return repository_view(record)

    async def list_repositories() -> list[dict[str, object]]:
        records = await asyncio.to_thread(repositories().list_repositories)
        return [repository_view(record) for record in records]

    async def repository(repository_id: str) -> dict[str, object]:
        try:
            record = await asyncio.to_thread(repositories().get_repository, repository_id)
        except KeyError as error:
            raise HTTPException(404, "Repository not found") from error
        return repository_view(record)

    async def scan_repository(repository_id: str) -> dict[str, object]:
        store = repositories()
        try:
            await asyncio.to_thread(RepositoryTools(store).is_current, repository_id)
            record = await asyncio.to_thread(store.get_repository, repository_id)
        except KeyError as error:
            raise HTTPException(404, "Repository not found") from error
        except (RepositoryPathError, ValueError) as error:
            raise HTTPException(409, str(error)) from error
        return repository_view(record)

    async def create_index(repository_id: str) -> dict[str, object]:
        tasks = cast(dict[str, asyncio.Task[object]], app.state.repository_index_tasks)
        if task := tasks.get(repository_id):
            if not task.done():
                raise HTTPException(409, "Repository index is already running")
        store = repositories()
        builder = indexer()
        try:
            record = await asyncio.to_thread(store.begin_index, repository_id)
        except KeyError as error:
            raise HTTPException(404, "Repository not found") from error

        async def build_in_background() -> None:
            try:
                await builder.build(repository_id)
            except Exception:
                logger.exception("Repository index build failed: %s", repository_id)
            finally:
                tasks.pop(repository_id, None)

        tasks[repository_id] = asyncio.create_task(build_in_background())
        return repository_view(record)

    async def prune_preview(repository_id: str) -> dict[str, object]:
        try:
            candidates = await asyncio.to_thread(
                repositories().prune_candidates,
                repository_id,
                protected_collection_ids=get_manager().store.collection_ids(),
            )
        except KeyError as error:
            raise HTTPException(404, "Repository not found") from error
        return {
            "candidates": [
                {"collection_id": item.collection_id, "status": item.status.value}
                for item in candidates
            ]
        }

    async def prune_snapshots(repository_id: str, body: SnapshotPruneBody) -> dict[str, object]:
        protected = get_manager().store.collection_ids()
        store = repositories()
        try:
            allowed = {
                item.collection_id
                for item in await asyncio.to_thread(
                    store.prune_candidates, repository_id, protected_collection_ids=protected
                )
            }
        except KeyError as error:
            raise HTTPException(404, "Repository not found") from error
        requested = set(body.collection_ids)
        if len(requested) != len(body.collection_ids) or not requested.issubset(allowed):
            raise HTTPException(
                409, "Snapshot cleanup preview is stale or contains protected evidence"
            )
        builder = indexer()
        vector_store = getattr(builder, "collections", None)
        if vector_store is None or not hasattr(vector_store, "delete_collection"):
            raise HTTPException(503, "Repository cleanup is not configured")
        for collection_id in sorted(requested):
            await asyncio.to_thread(vector_store.delete_collection, collection_id)
            await asyncio.to_thread(
                store.delete_pruned_snapshot,
                repository_id,
                collection_id,
                protected_collection_ids=protected,
            )
        return {"deleted_collection_ids": sorted(requested)}

    async def evidence(
        repository_id: str,
        passage_id: str,
        collection_id: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
    ) -> dict[str, object]:
        """Return a frozen indexed fragment, never bytes from the mutable work tree."""
        match = re.fullmatch(
            r"doc:(?P<doc_id>.+)(?:#|@)c(?P<chunk_no>0*[1-9][0-9]*)", unquote(passage_id)
        )
        if match is None:
            raise HTTPException(422, "Invalid repository passage ID")
        try:
            record = await asyncio.to_thread(repositories().get_repository, repository_id)
        except KeyError as error:
            raise HTTPException(404, "Repository not found") from error
        selected_collection = collection_id or record.current_collection_id
        if selected_collection is None:
            raise HTTPException(409, "Repository has no evidence snapshot")
        try:
            snapshot = await asyncio.to_thread(
                repositories().get_snapshot_for_collection, repository_id, selected_collection
            )
        except KeyError as error:
            raise HTTPException(404, "Repository evidence not found") from error
        if snapshot.status.value not in {"ready", "superseded"}:
            raise HTTPException(404, "Repository evidence not found")
        try:
            lookup = await asyncio.to_thread(
                frozen_evidence().read_passage,
                selected_collection,
                match["doc_id"],
                int(match["chunk_no"]),
            )
        except KeyError as error:
            raise HTTPException(404, "Repository evidence not found") from error
        passage = lookup.passage
        if passage.source.collection_id != selected_collection:
            raise HTTPException(404, "Repository evidence not found")
        locator = passage.source.locator
        start_line, end_line = locator.get("start_line"), locator.get("end_line")
        if type(start_line) is not int or type(end_line) is not int:
            raise HTTPException(422, "Evidence does not have line coordinates")
        return {
            "collection_id": passage.source.collection_id,
            "snapshot_status": snapshot.status.value,
            "retention_reason": (
                "当前证据快照，供新的调查会话使用"
                if selected_collection == record.current_collection_id
                else "历史会话绑定此快照，保留以确保引用仍可回看"
            ),
            "path": passage.source.path,
            "start_line": start_line,
            "end_line": end_line,
            "content": passage.text,
            "source_sha256": passage.source.sha256,
        }

    async def patch_preview(repository_id: str, body: PatchPreviewBody) -> dict[str, object]:
        """Validate and return a patch preview without opening a write-capable repository handle."""
        try:
            preview = await asyncio.to_thread(
                validate_patch_preview,
                RepositoryTools(repositories()),
                repository_id,
                body.summary,
                body.diff,
                [
                    PatchCitation(item.path, item.start_line, item.end_line)
                    for item in body.citations
                ],
            )
        except KeyError as error:
            raise HTTPException(404, "Repository not found") from error
        except (RepositoryPathError, ValueError) as error:
            raise HTTPException(409, str(error)) from error
        return {
            "summary": preview.summary,
            "diff": preview.diff,
            "citations": [
                {"path": item.path, "start_line": item.start_line, "end_line": item.end_line}
                for item in preview.citations
            ],
        }

    async def sessions() -> list[SessionSummary]:
        return await get_manager().list_sessions()

    async def create_session(body: SessionCreateBody | None = None) -> SessionView:
        repository_id = body.repository_id if body else None
        collection_id = body.collection_id if body else None
        if (repository_id is None) != (collection_id is None):
            raise HTTPException(422, "Repository and collection must be selected together")
        if repository_id is not None and collection_id is not None:
            try:
                record = await asyncio.to_thread(repositories().get_repository, repository_id)
            except KeyError as error:
                raise HTTPException(404, "Repository not found") from error
            if record.current_collection_id != collection_id or record.status != "ready":
                raise HTTPException(409, "Repository evidence is not ready for a new investigation")
        return await get_manager().create_session(
            repository_id=repository_id, collection_id=collection_id
        )

    async def session(session_id: str) -> SessionView:
        return await get_manager().get_session(session_id)

    async def message(session_id: str, body: MessageBody) -> TurnRecord:
        content = body.content.strip()
        if not content:
            raise HTTPException(422, "Message cannot be blank")
        return await get_manager().submit_message(session_id, body.request_id, content)

    async def resume(session_id: str, body: ContinueBody) -> TurnRecord:
        return await get_manager().resume_run(session_id, body.request_id)

    async def cancel(session_id: str) -> SessionView:
        await get_manager().cancel_run(session_id)
        return await get_manager().get_session(session_id)

    async def delete_session(session_id: str) -> DeleteSessionsResult:
        if not session_id.strip():
            raise HTTPException(422, "Session ID cannot be blank")
        return DeleteSessionsResult(deleted_ids=await get_manager().delete_sessions([session_id]))

    async def delete_sessions(body: DeleteSessionsBody) -> DeleteSessionsResult:
        return DeleteSessionsResult(
            deleted_ids=await get_manager().delete_sessions(body.session_ids)
        )

    async def events(session_id: str, after: Annotated[int, Query(ge=0)] = 0) -> list[Event]:
        await get_manager().get_session(session_id)
        return await asyncio.to_thread(get_manager().store.read, session_id, after)

    async def export(session_id: str) -> Response:
        await get_manager().get_session(session_id)
        rows = await asyncio.to_thread(get_manager().store.read, session_id)
        return Response(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": f'attachment; filename="{session_id}.jsonl"',
            },
        )

    async def stream(
        request: Request,
        session_id: str,
        after: Annotated[int, Query(ge=0)] = 0,
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> StreamingResponse:
        await get_manager().get_session(session_id)
        try:
            # 首次连接用查询参数，EventSource 自动重连会带 Last-Event-ID。
            # 取较大值避免退回旧游标；客户端仍需去重以应对边界处的重复交付。
            cursor = max(after, int(last_event_id or "0"))
        except ValueError as error:
            raise HTTPException(400, "Invalid Last-Event-ID") from error

        async def generate() -> AsyncGenerator[str]:
            nonlocal cursor
            heartbeat = 0
            while not await request.is_disconnected():
                # 从存储读已提交事件，不直接订阅模型内存回调；页面看到的每条消息均可回放。
                # 读取与下一次轮询之间产生的事件会在后续按 seq 补齐。
                rows = await asyncio.to_thread(get_manager().store.read, session_id, cursor)
                if not rows and not await asyncio.to_thread(get_manager().store.read, session_id):
                    yield "event: session-deleted\ndata: {}\n\n"
                    return
                for event in rows:
                    cursor = event["seq"]
                    yield (
                        f"id: {cursor}\nevent: trace\ndata: "
                        f"{json.dumps(event, ensure_ascii=False)}\n\n"
                    )
                heartbeat += 1
                # SSE 注释心跳不消耗事件序号，也不写会话日志，仅用于维持空闲连接。
                if heartbeat % 40 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    app.exception_handler(SessionNotFound)(not_found)
    app.exception_handler(Conflict)(conflict)
    app.get("/api/health")(health)
    app.get("/api/meta")(meta)
    app.get("/api/repositories/roots")(repository_roots)
    app.get("/api/repositories")(list_repositories)
    app.post("/api/repositories", status_code=201)(create_repository)
    app.get("/api/repositories/{repository_id}")(repository)
    app.post("/api/repositories/{repository_id}/scan")(scan_repository)
    app.post("/api/repositories/{repository_id}/indexes", status_code=202)(create_index)
    app.post("/api/repositories/{repository_id}/snapshots/prune-preview")(prune_preview)
    app.post("/api/repositories/{repository_id}/snapshots/prune")(prune_snapshots)
    app.get("/api/repositories/{repository_id}/evidence/{passage_id}")(evidence)
    app.post("/api/repositories/{repository_id}/patch-preview")(patch_preview)
    app.get("/api/sessions")(sessions)
    app.post("/api/sessions", status_code=201)(create_session)
    app.post("/api/sessions/batch-delete")(delete_sessions)
    app.delete("/api/sessions/{session_id}")(delete_session)
    app.get("/api/sessions/{session_id}")(session)
    app.post("/api/sessions/{session_id}/messages", status_code=202)(message)
    app.post("/api/sessions/{session_id}/continue", status_code=202)(resume)
    app.post("/api/sessions/{session_id}/cancel")(cancel)
    app.get("/api/sessions/{session_id}/events")(events)
    app.get("/api/sessions/{session_id}/export")(export)
    app.get("/api/sessions/{session_id}/stream")(stream)

    return app
