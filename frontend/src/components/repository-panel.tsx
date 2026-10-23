import { useState } from "react";
import { FolderSearch, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Repository, RepositoryRoot } from "@/state";

type Props = {
  roots: RepositoryRoot[];
  repositories: Repository[];
  selected: Repository | null;
  disabled: boolean;
  onSelect: (id: string) => void;
  onRegister: (rootId: string, relativePath: string) => Promise<void>;
  onScan: () => Promise<void>;
  onStartIndex: () => Promise<void>;
  onPrunePreview: () => Promise<{ collection_id: string; status: string }[]>;
  onPrune: (collectionIds: string[]) => Promise<void>;
  onError: (message: string) => void;
};

const labels: Record<string, string> = {
  unindexed: "未建立索引",
  indexing: "索引构建中",
  ready: "当前证据可用",
  stale: "索引已过期",
  failed: "索引失败",
};

/** Select and prepare one server-authorized repository before a chat session is created. */
export function RepositoryPanel({
  roots,
  repositories,
  selected,
  disabled,
  onSelect,
  onRegister,
  onScan,
  onStartIndex,
  onPrunePreview,
  onPrune,
  onError,
}: Props) {
  const [rootId, setRootId] = useState("");
  const [relativePath, setRelativePath] = useState("");
  const [working, setWorking] = useState(false);
  const [pruneCandidates, setPruneCandidates] = useState<
    { collection_id: string; status: string }[] | null
  >(null);
  const canRegister =
    !!rootId && !!relativePath.trim() && !working && !disabled;
  const canIndex =
    !!selected && selected.status !== "indexing" && !working && !disabled;

  async function run(task: () => Promise<void>) {
    setWorking(true);
    try {
      await task();
      onError("");
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error));
    } finally {
      setWorking(false);
    }
  }

  async function previewPrune() {
    setWorking(true);
    try {
      setPruneCandidates(await onPrunePreview());
      onError("");
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error));
    } finally {
      setWorking(false);
    }
  }

  return (
    <section className="space-y-2.5 border-b pb-4" aria-label="仓库索引">
      <div className="flex items-center gap-2 text-sm font-semibold">
        <FolderSearch className="size-4 text-primary" />
        仓库证据
      </div>
      <select
        aria-label="选择已登记仓库"
        className="h-9 w-full rounded-md border bg-background px-2 text-sm"
        value={selected?.id ?? ""}
        disabled={disabled || working}
        onChange={(event) => onSelect(event.target.value)}
      >
        <option value="">选择已登记仓库</option>
        {repositories.map((repository) => (
          <option key={repository.id} value={repository.id}>
            {repository.display_name} ·{" "}
            {labels[repository.status] ?? repository.status}
          </option>
        ))}
      </select>
      {selected && (
        <div className="rounded-md border bg-background p-2 text-xs">
          <p className="truncate font-mono">{selected.relative_path}</p>
          <p className="mt-1 text-muted-foreground">
            {labels[selected.status] ?? selected.status}
            {selected.current_collection_id
              ? ` · ${selected.current_collection_id}`
              : ""}
          </p>
          {selected.index_progress && (
            <p className="mt-1 text-muted-foreground">
              {selected.index_progress.phase} · 扫描{" "}
              {selected.index_progress.scanned_files} · 忽略{" "}
              {selected.index_progress.ignored_files} · 复用{" "}
              {selected.index_progress.reused_files} · 新分块{" "}
              {selected.index_progress.new_chunks} ·{" "}
              {Math.round(selected.index_progress.elapsed_ms / 1000)} 秒
            </p>
          )}
          <div className="mt-2 flex gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={!canIndex}
              onClick={() => run(onScan)}
            >
              扫描变更
            </Button>
            <Button
              size="sm"
              disabled={!canIndex}
              onClick={() => run(onStartIndex)}
            >
              <RefreshCw className="size-3.5" />
              {selected.status === "stale" ? "刷新索引" : "创建索引"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={working || disabled}
              onClick={() => void previewPrune()}
            >
              清理旧快照
            </Button>
          </div>
          {pruneCandidates && (
            <div className="mt-2 rounded border border-amber-300 bg-amber-50 p-2 text-amber-950">
              {pruneCandidates.length ? (
                <>
                  <p>
                    将删除 {pruneCandidates.length} 个不再被历史会话引用的快照。
                  </p>
                  <p className="mt-1 truncate font-mono">
                    {pruneCandidates
                      .map((item) => item.collection_id)
                      .join("、")}
                  </p>
                  <Button
                    className="mt-2"
                    size="sm"
                    variant="destructive"
                    disabled={working || disabled}
                    onClick={() =>
                      run(async () => {
                        await onPrune(
                          pruneCandidates.map((item) => item.collection_id),
                        );
                        setPruneCandidates(null);
                      })
                    }
                  >
                    确认删除 {pruneCandidates.length} 个快照
                  </Button>
                </>
              ) : (
                <p>没有可清理的旧快照；当前证据和历史会话引用均会保留。</p>
              )}
            </div>
          )}
        </div>
      )}
      <details className="rounded-md border bg-background px-2 py-1.5 text-xs">
        <summary className="cursor-pointer text-muted-foreground">
          登记新的相对路径
        </summary>
        <div className="mt-2 space-y-2">
          <select
            aria-label="选择允许根目录"
            className="h-8 w-full rounded border bg-background px-2"
            value={rootId}
            disabled={disabled || working}
            onChange={(event) => setRootId(event.target.value)}
          >
            <option value="">选择允许根目录</option>
            {roots.map((root) => (
              <option key={root.id} value={root.id}>
                {root.id}
              </option>
            ))}
          </select>
          <input
            aria-label="仓库相对路径"
            className="h-8 w-full rounded border bg-background px-2"
            placeholder="例如 sample-api"
            value={relativePath}
            disabled={disabled || working}
            onChange={(event) => setRelativePath(event.target.value)}
          />
          <Button
            className="w-full"
            size="sm"
            variant="outline"
            disabled={!canRegister}
            onClick={() =>
              run(async () => {
                await onRegister(rootId, relativePath.trim());
                setRelativePath("");
              })
            }
          >
            登记仓库
          </Button>
        </div>
      </details>
    </section>
  );
}
