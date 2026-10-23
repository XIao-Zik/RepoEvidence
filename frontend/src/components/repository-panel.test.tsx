// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { RepositoryPanel } from "./repository-panel";

it("previews obsolete snapshots and requires an explicit confirmation before pruning", async () => {
  const preview = vi
    .fn()
    .mockResolvedValue([
      { collection_id: "collection-v1", status: "superseded" },
    ]);
  const prune = vi.fn().mockResolvedValue(undefined);
  const user = userEvent.setup();

  render(
    <RepositoryPanel
      roots={[]}
      repositories={[]}
      selected={{
        id: "repo-1",
        root_id: "projects",
        relative_path: "sample-api",
        display_name: "sample-api",
        status: "ready",
        current_collection_id: "collection-v2",
      }}
      disabled={false}
      onSelect={vi.fn()}
      onRegister={vi.fn()}
      onScan={vi.fn()}
      onStartIndex={vi.fn()}
      onPrunePreview={preview}
      onPrune={prune}
      onError={vi.fn()}
    />,
  );

  await user.click(screen.getByRole("button", { name: "清理旧快照" }));
  await waitFor(() => expect(preview).toHaveBeenCalledTimes(1));
  expect(
    screen.getByText("将删除 1 个不再被历史会话引用的快照。"),
  ).toBeTruthy();
  expect(prune).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "确认删除 1 个快照" }));
  await waitFor(() => expect(prune).toHaveBeenCalledWith(["collection-v1"]));
});
