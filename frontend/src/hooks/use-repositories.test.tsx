// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { repositoryApi } from "@/lib/session-api";
import { useRepositories } from "./use-repositories";

afterEach(() => vi.restoreAllMocks());

it("loads safe roots and lets the user select a ready repository snapshot", async () => {
  vi.spyOn(repositoryApi, "listRoots").mockResolvedValue([{ id: "projects" }]);
  vi.spyOn(repositoryApi, "listRepositories").mockResolvedValue([
    {
      id: "repo-1",
      root_id: "projects",
      relative_path: "sample-api",
      display_name: "sample-api",
      status: "ready",
      current_collection_id: "collection-v2",
    },
  ]);
  const reportError = vi.fn();

  const { result } = renderHook(() => useRepositories(0, reportError));

  await waitFor(() => expect(result.current.selected?.id).toBe("repo-1"));
  await act(async () => result.current.select("repo-1"));

  expect(result.current.roots).toEqual([{ id: "projects" }]);
  expect(result.current.readySelection).toEqual({
    repositoryId: "repo-1",
    collectionId: "collection-v2",
  });
  expect(reportError).toHaveBeenLastCalledWith("");
});

it("starts a new immutable index through the selected repository only", async () => {
  vi.spyOn(repositoryApi, "listRoots").mockResolvedValue([{ id: "projects" }]);
  vi.spyOn(repositoryApi, "listRepositories").mockResolvedValue([
    {
      id: "repo-1",
      root_id: "projects",
      relative_path: "sample-api",
      display_name: "sample-api",
      status: "stale",
      current_collection_id: "collection-v1",
    },
  ]);
  const startIndex = vi.spyOn(repositoryApi, "startIndex").mockResolvedValue({
    id: "repo-1",
    root_id: "projects",
    relative_path: "sample-api",
    display_name: "sample-api",
    status: "indexing",
    current_collection_id: "collection-v1",
  });
  const { result } = renderHook(() => useRepositories(0, vi.fn()));
  await waitFor(() => expect(result.current.selected?.id).toBe("repo-1"));

  await act(async () => result.current.startIndex());

  expect(startIndex).toHaveBeenCalledWith("repo-1");
});

it("registers a repository using a root ID and relative path only", async () => {
  vi.spyOn(repositoryApi, "listRoots").mockResolvedValue([{ id: "projects" }]);
  vi.spyOn(repositoryApi, "listRepositories").mockResolvedValue([]);
  const register = vi.spyOn(repositoryApi, "register").mockResolvedValue({
    id: "repo-1",
    root_id: "projects",
    relative_path: "sample-api",
    display_name: "sample-api",
    status: "unindexed",
    current_collection_id: null,
  });
  const { result } = renderHook(() => useRepositories(0, vi.fn()));
  await waitFor(() =>
    expect(result.current.roots).toEqual([{ id: "projects" }]),
  );

  await act(async () => result.current.register("projects", "sample-api"));

  expect(register).toHaveBeenCalledWith("projects", "sample-api");
});
