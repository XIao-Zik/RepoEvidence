import type { FrozenEvidence } from "@/components/evidence-dialog";
import type {
  Meta,
  Repository,
  RepositoryRoot,
  Session,
  TraceEvent,
} from "@/state";

async function request<T>(
  path: string,
  method: "GET" | "POST" | "DELETE",
  body?: unknown,
): Promise<T> {
  const response = await fetch(
    `/api${path}`,
    method === "GET"
      ? undefined
      : {
          method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`;
    try {
      const error = await response.json();
      if (typeof error.detail === "string") detail = error.detail;
    } catch {
      /* 非 JSON 错误正文使用 HTTP 状态提示。 */
    }
    throw new Error(detail);
  }
  return response.json();
}

// HTTP 路径和请求格式只在此处定义；界面调用含义明确的动作。
export const sessionApi = {
  deleteSession: (id: string) =>
    request<{ deleted_ids: string[] }>(
      `/sessions/${encodeURIComponent(id)}`,
      "DELETE",
    ),
  deleteSessions: (ids: string[]) =>
    request<{ deleted_ids: string[] }>("/sessions/batch-delete", "POST", {
      session_ids: ids,
    }),
  getMeta: () => request<Meta>("/meta", "GET"),
  listSessions: () => request<Session[]>("/sessions", "GET"),
  createSession: (selection?: { repositoryId: string; collectionId: string }) =>
    request<Session>(
      "/sessions",
      "POST",
      selection
        ? {
            repository_id: selection.repositoryId,
            collection_id: selection.collectionId,
          }
        : {},
    ),
  getEvents: (id: string) =>
    request<TraceEvent[]>(`/sessions/${id}/events`, "GET"),
  submitMessage: (id: string, requestId: string, content: string) =>
    request(`/sessions/${id}/messages`, "POST", {
      request_id: requestId,
      content,
    }),
  resumeRun: (id: string, requestId: string) =>
    request(`/sessions/${id}/continue`, "POST", { request_id: requestId }),
  cancelRun: (id: string) => request(`/sessions/${id}/cancel`, "POST", {}),
};

export const repositoryApi = {
  listRoots: () => request<RepositoryRoot[]>("/repositories/roots", "GET"),
  listRepositories: () => request<Repository[]>("/repositories", "GET"),
  register: (rootId: string, relativePath: string) =>
    request<Repository>("/repositories", "POST", {
      root_id: rootId,
      relative_path: relativePath,
    }),
  scan: (repositoryId: string) =>
    request<Repository>(
      `/repositories/${encodeURIComponent(repositoryId)}/scan`,
      "POST",
      {},
    ),
  startIndex: (repositoryId: string) =>
    request<Repository>(
      `/repositories/${encodeURIComponent(repositoryId)}/indexes`,
      "POST",
      {},
    ),
  previewSnapshotPrune: (repositoryId: string) =>
    request<{ candidates: { collection_id: string; status: string }[] }>(
      `/repositories/${encodeURIComponent(repositoryId)}/snapshots/prune-preview`,
      "POST",
      {},
    ).then((result) => result.candidates),
  pruneSnapshots: (repositoryId: string, collectionIds: string[]) =>
    request<{ deleted_collection_ids: string[] }>(
      `/repositories/${encodeURIComponent(repositoryId)}/snapshots/prune`,
      "POST",
      { collection_ids: collectionIds },
    ),
  getEvidence: (
    repositoryId: string,
    passageId: string,
    collectionId?: string,
  ) =>
    request<FrozenEvidence>(
      `/repositories/${encodeURIComponent(repositoryId)}/evidence/${encodeURIComponent(passageId)}${collectionId ? `?collection_id=${encodeURIComponent(collectionId)}` : ""}`,
      "GET",
    ),
};
export const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : String(error);
