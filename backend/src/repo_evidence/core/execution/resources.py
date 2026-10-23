"""启动时装配只读资源，独立负责每轮数据版本核对。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Protocol

from repo_evidence.core.agent.trace import Tracer
from repo_evidence.core.config import Settings
from repo_evidence.core.json import JsonObject
from repo_evidence.core.knowledge.client import KnowledgeClient
from repo_evidence.core.knowledge.contracts import Collection, DocumentRetriever, VectorStore
from repo_evidence.core.llm.embedding import EmbeddingClient
from repo_evidence.core.llm.embedding.openai_embedding import OpenAiEmbeddingModel

from .errors import Conflict


class KnowledgeReader(Protocol):
    """运行前核对知识版本所需的最小只读能力，不提供构建或策略性检索。"""

    def get_collection(self, collection_id: str) -> Collection:
        """读取集合的版本与构建身份，用于生成本轮配置快照。"""
        ...

    def close(self) -> None:
        """释放知识连接；运行器结束后由资源所有者调用。"""
        ...


class RuntimeResources:
    """知识连接在生命周期内复用，版本快照则每轮重新读取。

    测试可以注入只读资源；正常启动通过 from_settings 装配。构建知识库的 writer
    不属于聊天服务资源。close 必须在所有 Run 结束后调用。
    """

    def __init__(
        self,
        settings: Settings,
        knowledge: KnowledgeReader | None = None,
        vectors: VectorStore | None = None,
        retriever: Callable[[Tracer], DocumentRetriever] | None = None,
    ):
        """保存配置和知识连接。

        检索客户端按轮装配，因为追踪是按轮的；retriever 用于注入替代实现，
        测试和演示据此避免依赖真实向量库。
        """
        self.settings: Settings = settings
        self.knowledge: KnowledgeReader | None = knowledge
        self.vectors: VectorStore | None = vectors
        self.retriever: Callable[[Tracer], DocumentRetriever] | None = retriever

    def document_retriever(self, tracer: Tracer) -> DocumentRetriever | None:
        """为一轮运行装配带追踪的检索客户端；连接池仍由本对象持有并复用。"""
        if self.retriever is not None:
            return self.retriever(tracer)
        if self.vectors is None or self.settings.embedding is None:
            return None
        embeddings = EmbeddingClient(OpenAiEmbeddingModel(self.settings.embedding), tracer)
        return KnowledgeClient(self.vectors, embeddings, tracer)

    @classmethod
    def from_settings(cls, settings: Settings) -> RuntimeResources:
        """按配置打开只读知识连接；返回需显式关闭的资源。"""
        from repo_evidence.core.knowledge import PostgresVectorStore

        knowledge = (
            PostgresVectorStore(os.environ[settings.knowledge_url_env])
            if settings.repository_roots
            else None
        )
        # 检索与版本核对共用同一个连接池；管理器结束全部任务后再统一关闭。
        return cls(settings, knowledge, knowledge)

    def close(self) -> None:
        """关闭本对象持有的知识读取器；调用前应结束所有使用它的 Run。"""
        if self.knowledge:
            self.knowledge.close()

    def _snapshot(self, collection_id: str | None = None) -> JsonObject:
        """同步校验会话绑定的知识版本并形成配置快照。"""
        snapshot = self.settings.snapshot()
        active_collection = collection_id
        if self.knowledge and active_collection:
            collection = self.knowledge.get_collection(active_collection)
            if collection["status"] != "ready":
                raise Conflict("Knowledge collection is not ready")
            snapshot["knowledge"] = {
                key: collection[key]
                for key in (
                    "collection_id",
                    "dataset_release_id",
                    "dataset_manifest_sha256",
                    "build_config",
                    "dimensions",
                )
            }
        return snapshot

    async def snapshot(
        self, previous: JsonObject | None = None, *, collection_id: str | None = None
    ) -> JsonObject:
        """只校验并记录集合版本；查询策略和会话调度不进入资源层。"""
        current = await asyncio.to_thread(self._snapshot, collection_id)
        if previous is not None and any(
            previous.get(key) != current.get(key) for key in ("knowledge",)
        ):
            raise Conflict("Data version changed; create a new session")
        return current
