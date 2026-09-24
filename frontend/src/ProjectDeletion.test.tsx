import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";
import { ConfirmationHost } from "./confirmation";
import { ProjectDeleteButton, ProjectDeletionHistory, type DeletionReceipt } from "./ProjectDeletion";

vi.mock("./api", () => ({
  api: vi.fn(), commandHeaders: () => ({ "Idempotency-Key": "test-delete-key" }),
  jsonBody: JSON.stringify,
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); vi.useRealTimers(); });

const preview = {
  project_id: "project-a", title: "测试小说", binding_sha256: "a".repeat(64),
  counts: { chapters: 8, state_versions: 12, writing_sessions: 3 },
  file_count: 5, estimated_file_bytes: 2048, shared_files_preserved: 1,
  blockers: [], exclusions: ["仅保留不含小说内容的删除凭证和费用摘要"], cost_summary: {},
};
const receipt: DeletionReceipt = {
  deletion_id: "delete-a", project_id: "project-a", status: "completed", database_deleted: true,
  counts: {}, cost_summary: {}, cleanup_error: null, created_at: "2026-09-07T01:00:00Z",
  completed_at: "2026-09-07T01:01:00Z",
};

function renderDelete(onResult = vi.fn(async () => undefined)) {
  render(<><ConfirmationHost /><ProjectDeleteButton projectId="project-a" title="测试小说"
    disabled={false} onResult={onResult} /></>);
  fireEvent.click(screen.getByRole("button", { name: "永久删除小说 测试小说" }));
  return onResult;
}

describe("permanent novel deletion", () => {
  it("shows elapsed read-only progress and lets the author cancel the preview", async () => {
    vi.useFakeTimers();
    let resolvePreview!: (value: typeof preview) => void;
    vi.mocked(api).mockReturnValueOnce(new Promise((resolve) => { resolvePreview = resolve; }));
    renderDelete();
    await act(async () => { vi.advanceTimersByTime(3000); });
    expect(screen.getByRole("status")).toHaveTextContent("已等待 3 秒；尚未删除任何数据");
    const signal = vi.mocked(api).mock.calls[0][1]?.signal;
    fireEvent.click(screen.getByRole("button", { name: "取消核对" }));
    expect(signal?.aborted).toBe(true);
    expect(screen.getByRole("button", { name: "永久删除小说 测试小说" })).toBeEnabled();
    await act(async () => { resolvePreview(preview); });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(api).toHaveBeenCalledTimes(1);
  });

  it("ends a hung preview after 60 seconds without automatically retrying or deleting", async () => {
    vi.useFakeTimers();
    vi.mocked(api).mockReturnValueOnce(new Promise(() => undefined));
    renderDelete();
    await act(async () => { vi.advanceTimersByTime(60_000); });
    expect(screen.getByRole("alert")).toHaveTextContent("核对超过 60 秒");
    expect(screen.getByRole("alert")).toHaveTextContent("未提交删除");
    expect(screen.getByRole("button", { name: "永久删除小说 测试小说" })).toBeEnabled();
    expect(vi.mocked(api).mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(api).toHaveBeenCalledTimes(1);
  });

  it("ignores a cancelled preview even after a new preview reaches confirmation", async () => {
    let resolvePreview!: (value: typeof preview) => void;
    vi.mocked(api).mockReturnValueOnce(new Promise((resolve) => { resolvePreview = resolve; }))
      .mockResolvedValueOnce(preview);
    renderDelete();
    fireEvent.click(screen.getByRole("button", { name: "取消核对" }));
    fireEvent.click(screen.getByRole("button", { name: "永久删除小说 测试小说" }));
    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
    await act(async () => { resolvePreview({ ...preview, project_id: "stale-project" }); });
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "永久删除小说 测试小说" })).toHaveTextContent("等待确认");
    expect(api).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
  });

  it("aborts an unmounted preview without showing a late confirmation", async () => {
    let resolvePreview!: (value: typeof preview) => void;
    vi.mocked(api).mockReturnValueOnce(new Promise((resolve) => { resolvePreview = resolve; }));
    renderDelete();
    const signal = vi.mocked(api).mock.calls[0][1]?.signal;
    cleanup();
    expect(signal?.aborted).toBe(true);
    await act(async () => { resolvePreview(preview); });
    expect(api).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("distinguishes submitted deletion from the cancellable preview", async () => {
    let resolveDeletion!: (value: DeletionReceipt) => void;
    vi.mocked(api).mockResolvedValueOnce(preview)
      .mockReturnValueOnce(new Promise((resolve) => { resolveDeletion = resolve; }));
    const onResult = renderDelete();
    const confirmation = await screen.findByRole("button", { name: "永久删除整本小说" });
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "测试小说" } });
    fireEvent.click(confirmation);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("已提交永久删除"));
    expect(screen.queryByRole("button", { name: "取消核对" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "永久删除小说 测试小说" })).toHaveTextContent("删除中");
    await act(async () => { resolveDeletion(receipt); });
    expect(onResult).toHaveBeenCalledWith(receipt);
  });

  it("requires the complete title and submits the exact preview binding", async () => {
    vi.mocked(api).mockResolvedValueOnce(preview).mockResolvedValueOnce(receipt);
    const onResult = renderDelete();
    const confirmation = await screen.findByRole("button", { name: "永久删除整本小说" });
    expect(confirmation).toBeDisabled();
    expect(api).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "测试" } });
    expect(confirmation).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "测试小说" } });
    fireEvent.click(confirmation);
    await waitFor(() => expect(onResult).toHaveBeenCalledWith(receipt));
    expect(api).toHaveBeenLastCalledWith("/api/projects/project-a/delete", expect.objectContaining({
      method: "POST", body: JSON.stringify({ confirmed: true, confirmed_title: "测试小说", binding_sha256: preview.binding_sha256 }),
    }));
  });

  it("does not send a deletion after cancellation", async () => {
    vi.mocked(api).mockResolvedValueOnce(preview);
    renderDelete();
    fireEvent.click(await screen.findByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(api).toHaveBeenCalledTimes(1);
  });

  it("shows backend blockers without offering destructive confirmation", async () => {
    vi.mocked(api).mockResolvedValueOnce({ ...preview, blockers: ["作品仍有执行中的模型请求"] });
    renderDelete();
    expect(await screen.findByRole("alert")).toHaveTextContent("执行中的模型请求");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(api).toHaveBeenCalledTimes(1);
  });

  it("rejects a mismatched project preview", async () => {
    vi.mocked(api).mockResolvedValueOnce({ ...preview, project_id: "project-b" });
    renderDelete();
    expect(await screen.findByRole("alert")).toHaveTextContent("作品信息已变化");
    expect(api).toHaveBeenCalledTimes(1);
  });

  it("never automatically retries a failed deletion request", async () => {
    vi.mocked(api).mockResolvedValueOnce(preview).mockRejectedValueOnce(new Error("连接中断"));
    const onResult = renderDelete();
    const confirmation = await screen.findByRole("button", { name: "永久删除整本小说" });
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "测试小说" } });
    fireEvent.click(confirmation);
    expect(await screen.findByRole("alert")).toHaveTextContent("不会自动重发");
    expect(api).toHaveBeenCalledTimes(2);
    expect(onResult).not.toHaveBeenCalled();
  });

  it("keeps pending cleanup visible and requires an explicit local retry", async () => {
    const pending: DeletionReceipt = { ...receipt, status: "cleanup_pending", completed_at: null,
      cleanup_error: "文件占用" };
    const onResult = vi.fn(async () => undefined);
    vi.mocked(api).mockResolvedValueOnce(receipt);
    render(<><ConfirmationHost /><ProjectDeletionHistory latest={pending} onResult={onResult}
      onRefresh={vi.fn(async (items: DeletionReceipt[]) => items)} /></>);
    expect(await screen.findByText("数据库已删除，文件清理未完成")).toBeInTheDocument();
    expect(api).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "继续清理残留" }));
    expect(api).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "继续本地清理" }));
    await waitFor(() => expect(onResult).toHaveBeenCalledWith(receipt));
    expect(api).toHaveBeenCalledTimes(1);
    expect(api).toHaveBeenCalledWith("/api/project-deletions/delete-a/cleanup", expect.objectContaining({
      method: "POST", body: JSON.stringify({ confirmed: true }),
    }));
  });

  it("reads deletion receipts after reload without resending a destructive request", async () => {
    const onRefresh = vi.fn(async (items: DeletionReceipt[]) => items);
    vi.mocked(api).mockResolvedValueOnce([receipt]);
    render(<ProjectDeletionHistory latest={null} onResult={vi.fn(async () => undefined)}
      onRefresh={onRefresh} />);
    fireEvent.click(screen.getByRole("button", { name: "删除记录与残留清理" }));
    expect(await screen.findByText("清理完成")).toBeInTheDocument();
    expect(onRefresh).toHaveBeenCalledWith([receipt]);
    expect(api).toHaveBeenCalledTimes(1);
    expect(api).toHaveBeenCalledWith("/api/project-deletions");
  });
});
