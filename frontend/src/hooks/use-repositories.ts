import { useCallback, useEffect, useMemo, useState } from "react";
import { errorMessage, repositoryApi } from "@/lib/session-api";
import type { Repository, RepositoryRoot } from "@/state";

export type ReadyRepositorySelection = {
  repositoryId: string;
  collectionId: string;
};

/** Loads only server-authorized repositories; host paths never enter browser state. */
export function useRepositories(
  retry: number,
  reportError: (message: string) => void,
) {
  const [roots, setRoots] = useState<RepositoryRoot[]>([]);
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [nextRoots, nextRepositories] = await Promise.all([
      repositoryApi.listRoots(),
      repositoryApi.listRepositories(),
    ]);
    setRoots(nextRoots);
    setRepositories(nextRepositories);
    setSelectedId((current) => {
      if (
        current &&
        nextRepositories.some((repository) => repository.id === current)
      )
        return current;
      return (
        nextRepositories.find((repository) => repository.status === "ready")
          ?.id ??
        nextRepositories[0]?.id ??
        null
      );
    });
  }, []);

  useEffect(() => {
    refresh()
      .then(() => reportError(""))
      .catch((error) => reportError(errorMessage(error)));
  }, [retry, refresh, reportError]);

  const selected = useMemo(
    () =>
      repositories.find((repository) => repository.id === selectedId) ?? null,
    [repositories, selectedId],
  );
  useEffect(() => {
    if (selected?.status !== "indexing") return;
    const timer = window.setInterval(() => {
      refresh().catch((error) => reportError(errorMessage(error)));
    }, 1500);
    return () => window.clearInterval(timer);
  }, [selected?.status, refresh, reportError]);
  const readySelection = useMemo<ReadyRepositorySelection | null>(() => {
    if (selected?.status !== "ready" || !selected.current_collection_id)
      return null;
    return {
      repositoryId: selected.id,
      collectionId: selected.current_collection_id,
    };
  }, [selected]);

  function replaceRepository(record: Repository): void {
    setRepositories((current) => {
      const retained = current.filter(
        (repository) => repository.id !== record.id,
      );
      return [...retained, record].sort((left, right) =>
        left.display_name.localeCompare(right.display_name),
      );
    });
    setSelectedId(record.id);
  }

  async function register(rootId: string, relativePath: string): Promise<void> {
    replaceRepository(await repositoryApi.register(rootId, relativePath));
  }

  async function scan(): Promise<void> {
    if (!selected) return;
    replaceRepository(await repositoryApi.scan(selected.id));
  }

  async function startIndex(): Promise<void> {
    if (!selected) return;
    replaceRepository(await repositoryApi.startIndex(selected.id));
  }

  async function prunePreview(): Promise<
    { collection_id: string; status: string }[]
  > {
    if (!selected) return [];
    return repositoryApi.previewSnapshotPrune(selected.id);
  }

  async function prune(collectionIds: string[]): Promise<void> {
    if (!selected) return;
    await repositoryApi.pruneSnapshots(selected.id, collectionIds);
    await refresh();
  }

  return {
    roots,
    repositories,
    selected,
    readySelection,
    select: setSelectedId,
    refresh,
    register,
    scan,
    startIndex,
    prunePreview,
    prune,
  };
}
