"""Unified, read-only agent workflow for repository evidence investigations."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable

from repo_evidence.core.agent import AgentTurn, Budget
from repo_evidence.core.execution.contracts import TurnInput, TurnResult
from repo_evidence.core.execution.runtime import AgentRuntime
from repo_evidence.core.execution.stage import StageCard
from repo_evidence.core.json import (
    JsonObject,
    require_int,
    require_list,
    require_object,
    require_text,
    to_json_object,
)
from repo_evidence.core.knowledge.contracts import Passage, PassageSource
from repo_evidence.core.llm.chat import ChatModel
from repo_evidence.core.llm.chat.messages import ModelOutputError
from repo_evidence.core.tools import Tool
from repo_evidence.repositories.patches import PatchCitation, validate_patch_preview
from repo_evidence.repositories.tools import RepositoryToolError, RepositoryTools

SYSTEM = """你是 RepoEvidence 代码库证据助手。
只将本轮工具返回的内容称作已验证事实；每个代码结论都必须引用 path:start-end。
必要时先搜索仓库；可分别检索技术文档或文本日志，再用 read_file 读取有限行数。
可用 list_directory 了解安全文件布局。不要访问、修改、执行或提交用户仓库。
输出包含：结论、证据、排除项、下一步、不确定性。"""

CITATION = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+):"
    r"(?P<start>[1-9][0-9]*)-(?P<end>[1-9][0-9]*)"
)
CITATION_CORRECTION = (
    "回答里的这些代码位置不在本轮已检索或读取的证据范围内：{citations}。"
    "请只引用实际证据中的 path:start-end，或明确说明无法确认。"
)


class EvidenceWorkflow:
    """Offer repository search and bounded source reads under one agent budget."""

    id = "repo-evidence-agent"
    version = "1"
    max_model_calls = 5
    max_tool_calls = 4
    card = StageCard(
        label="代码证据助手",
        description="检索仓库并读取精确行号，以可核查证据回答问题。",
        examples=["认证函数在哪里？", "这个配置从哪里读取？"],
        reply_hint="回答会附带仓库路径、行号和当前索引版本。",
    )

    def __init__(
        self,
        model: ChatModel,
        repository_tools: RepositoryTools,
        prepare_query: Callable[[str], str],
    ) -> None:
        self.model = model
        self.repository_tools = repository_tools
        self.prepare_query = prepare_query

    async def run(self, turn: TurnInput, runtime: AgentRuntime) -> TurnResult:
        if turn.repository_id is None or turn.collection_id is None:
            return TurnResult("请先选择并完成索引一个仓库。", {})
        repository_id = turn.repository_id
        collection_id = turn.collection_id
        if not await asyncio.to_thread(self.repository_tools.is_current, repository_id):
            return TurnResult("检测到代码已变更，请先刷新索引后再基于当前代码提问。", {})
        if not await asyncio.to_thread(
            self.repository_tools.is_current_collection, repository_id, collection_id
        ):
            return TurnResult(
                "当前会话绑定的证据快照已更新，请创建新会话后再基于当前代码提问。", {}
            )

        agent = AgentTurn.start(
            turn,
            runtime,
            SYSTEM,
            self.model,
            Budget(self.max_model_calls, self.max_tool_calls),
        )
        done: set[str] = set()

        async def search_repository(arguments: JsonObject) -> JsonObject:
            query = " ".join(require_text(arguments["query"]).split())
            if refused := self._claim(agent, done, "search", query):
                return {"query": query, "passages": [], "notice": refused}
            passages = await agent.search(self.prepare_query(query), limit=8)
            return {
                "query": query,
                "passages": [to_json_object(passage) for passage in passages],
                "notice": "未检索到匹配的仓库片段。" if not passages else None,
            }

        async def search_by_content_type(
            arguments: JsonObject, content_type: str, tool_name: str
        ) -> JsonObject:
            query = " ".join(require_text(arguments["query"]).split())
            if refused := self._claim(agent, done, tool_name, query):
                return {"query": query, "passages": [], "notice": refused}
            passages = await agent.search(
                self.prepare_query(query), limit=8, filters={"content_type": content_type}
            )
            return {
                "query": query,
                "passages": [to_json_object(passage) for passage in passages],
                "notice": "未检索到匹配片段。" if not passages else None,
            }

        async def search_docs(arguments: JsonObject) -> JsonObject:
            return await search_by_content_type(arguments, "document", "search_docs")

        async def search_logs(arguments: JsonObject) -> JsonObject:
            return await search_by_content_type(arguments, "log", "search_logs")

        async def read_file(arguments: JsonObject) -> JsonObject:
            path = require_text(arguments["path"])
            start_line = require_int(arguments["start_line"])
            end_line = require_int(arguments["end_line"])
            target = f"{path}:{start_line}-{end_line}"
            if refused := self._claim(agent, done, "read", target):
                return {"path": path, "notice": refused}
            try:
                result = await asyncio.to_thread(
                    self.repository_tools.read_file,
                    repository_id,
                    path,
                    start_line,
                    end_line,
                )
            except RepositoryToolError as error:
                return {"path": path, "notice": str(error)}
            agent.retrieved.add([self._read_evidence(collection_id, result)])
            return to_json_object(result)

        async def list_directory(arguments: JsonObject) -> JsonObject:
            path = str(arguments.get("path", "."))
            depth = arguments.get("depth", 2)
            if type(depth) is not int:
                return {"path": path, "notice": "Directory depth must be an integer"}
            target = f"{path}:{depth}"
            if refused := self._claim(agent, done, "list_directory", target):
                return {"path": path, "entries": [], "notice": refused}
            try:
                result = await asyncio.to_thread(
                    self.repository_tools.list_directory, repository_id, path, depth
                )
            except RepositoryToolError as error:
                return {"path": path, "entries": [], "notice": str(error)}
            return {"path": result.path, "entries": list(result.entries)}

        async def preview_patch(arguments: JsonObject) -> JsonObject:
            summary = require_text(arguments["summary"])
            diff = require_text(arguments["diff"])
            citations = [
                PatchCitation(
                    require_text(item["path"]),
                    require_int(item["start_line"]),
                    require_int(item["end_line"]),
                )
                for value in require_list(arguments["citations"])
                for item in [require_object(value)]
            ]
            target = hashlib.sha256(diff.encode("utf-8")).hexdigest()
            if refused := self._claim(agent, done, "preview_patch", target):
                return {"notice": refused}
            unsupported = [
                f"{citation.path}:{citation.start_line}-{citation.end_line}"
                for citation in citations
                if not any(
                    _covers(passage, citation.path, citation.start_line, citation.end_line)
                    for passage in agent.retrieved.all()
                )
            ]
            if unsupported:
                return {
                    "notice": "补丁预览只能引用本轮已检索或读取的证据：" + "、".join(unsupported)
                }
            try:
                preview = await asyncio.to_thread(
                    validate_patch_preview,
                    self.repository_tools,
                    repository_id,
                    summary,
                    diff,
                    citations,
                )
            except (RepositoryToolError, ValueError) as error:
                return {"notice": str(error)}
            return {
                "summary": preview.summary,
                "diff": preview.diff,
                "citations": [to_json_object(item) for item in preview.citations],
                "notice": "这是只供审核的补丁预览，系统没有写入或应用任何文件。",
            }

        agent.offer(
            Tool(
                "search_repository",
                "在当前已索引仓库中按语义检索代码与文档片段。",
                {
                    "type": "object",
                    "properties": {"query": {"type": "string", "minLength": 1}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                search_repository,
            ),
            Tool(
                "read_file",
                "读取当前仓库一个已获授权文件的有限行范围。",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "minLength": 1},
                        "start_line": {"type": "integer", "minimum": 1},
                        "end_line": {"type": "integer", "minimum": 1},
                    },
                    "required": ["path", "start_line", "end_line"],
                    "additionalProperties": False,
                },
                read_file,
            ),
            Tool(
                "search_docs",
                "仅在当前快照的技术文档中语义检索，不检索源码或日志。",
                {
                    "type": "object",
                    "properties": {"query": {"type": "string", "minLength": 1}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                search_docs,
            ),
            Tool(
                "search_logs",
                "仅在当前快照已收录的文本日志中语义检索；没有日志时会明确返回空结果。",
                {
                    "type": "object",
                    "properties": {"query": {"type": "string", "minLength": 1}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                search_logs,
            ),
            Tool(
                "list_directory",
                "列出当前快照中一个目录的安全文件路径，深度最多 3 层，不返回文件内容。",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "default": "."},
                        "depth": {"type": "integer", "minimum": 1, "maximum": 3, "default": 2},
                    },
                    "additionalProperties": False,
                },
                list_directory,
            ),
            Tool(
                "preview_patch",
                "基于本轮已读取的当前证据生成统一 diff 预览；只返回审核文本，绝不写入或应用。",
                {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "minLength": 1, "maxLength": 4000},
                        "diff": {"type": "string", "minLength": 1, "maxLength": 100000},
                        "citations": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 32,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string", "minLength": 1},
                                    "start_line": {"type": "integer", "minimum": 1},
                                    "end_line": {"type": "integer", "minimum": 1},
                                },
                                "required": ["path", "start_line", "end_line"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["summary", "diff", "citations"],
                    "additionalProperties": False,
                },
                preview_patch,
            ),
        )
        while agent.may_think():
            reply = await agent.think()
            if reply.is_answer:
                if await self._dispute_unsupported_citations(agent, reply.text, reply.call_id):
                    continue
                return agent.accept(reply)
            for action in reply.actions:
                agent.observe(await agent.perform(action))
        raise ModelOutputError("代码证据助手在模型调用预算内未生成有效回答")

    @staticmethod
    def _claim(agent: AgentTurn, done: set[str], tool: str, target: str) -> str | None:
        key = f"{tool}:{target.casefold()}"
        if key in done:
            return "此查询或文件范围已经读取，请使用已有结果或换一个范围。"
        if not agent.budget.has_tool_calls_left():
            return "工具预算已耗尽，请基于已有证据说明限制。"
        agent.budget.spend_tool_call()
        done.add(key)
        return None

    @staticmethod
    def _read_evidence(collection_id: str, result: object) -> Passage:
        """Promote a verified bounded file read into the same citation view as search results."""
        from repo_evidence.repositories.tools import FileReadResult

        if not isinstance(result, FileReadResult):
            raise TypeError("Expected a bounded repository file read")
        doc_id = "file-" + hashlib.sha256(result.path.encode("utf-8")).hexdigest()[:24]
        passage_id = (
            f"{doc_id}#read-{result.start_line}-{result.end_line}-{result.source_sha256[:12]}"
        )
        return Passage(
            id=passage_id,
            title=result.path,
            text=result.content,
            score=1.0,
            source=PassageSource(
                collection_id=collection_id,
                doc_id=doc_id,
                chunk_no=result.start_line,
                path=result.path,
                sha256=result.source_sha256,
                locator={"start_line": result.start_line, "end_line": result.end_line},
                metadata={"kind": "bounded_file_read"},
            ),
        )

    @staticmethod
    async def _dispute_unsupported_citations(agent: AgentTurn, text: str, call_id: str) -> bool:
        """Reject fabricated locations; an answer with no location may still state uncertainty."""
        unsupported = _unsupported_citations(text, agent.retrieved.all())
        if not unsupported or not agent.budget.may_think() or agent.budget.must_answer():
            return False
        await agent.runtime.tracer.emit("answer.disputed", {"citations": unsupported}, call_id)
        agent.messages.add_system_message(
            CITATION_CORRECTION.format(citations="、".join(unsupported))
        )
        return True


def _unsupported_citations(text: str, evidence: list[Passage]) -> list[str]:
    """Return cited path/ranges that are not fully covered by a frozen evidence fragment."""
    unsupported: list[str] = []
    for match in CITATION.finditer(text):
        path = match["path"]
        start_line, end_line = int(match["start"]), int(match["end"])
        citation = match.group(0)
        if end_line < start_line or not any(
            _covers(passage, path, start_line, end_line) for passage in evidence
        ):
            unsupported.append(citation)
    return sorted(set(unsupported))


def _covers(passage: Passage, path: str, start_line: int, end_line: int) -> bool:
    """Check a citation against line metadata without trusting model-provided coordinates."""
    locator = passage.source.locator
    available_start = locator.get("start_line")
    available_end = locator.get("end_line")
    return (
        passage.source.path == path
        and type(available_start) is int
        and type(available_end) is int
        and available_start <= start_line <= end_line <= available_end
    )
