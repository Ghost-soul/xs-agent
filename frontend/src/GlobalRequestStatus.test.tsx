import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GlobalRequestStatus } from "./GlobalRequestStatus";
import { receiveRemoteProjectOperations, type ProjectOperation } from "./projectOperations";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.removeItem("novel-writer:system-task-notifications");
});

describe("GlobalRequestStatus", () => {
  it("observes tasks in a hidden page when the author enabled system notifications", async () => {
    vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    vi.stubGlobal("Notification", Object.assign(vi.fn(), { permission: "granted" }));
    window.localStorage.setItem("novel-writer:system-task-notifications", "enabled");
    const fetchMock = vi.fn().mockResolvedValue(new Response("[]"));
    vi.stubGlobal("fetch", fetchMock);
    render(<GlobalRequestStatus locallyBusy={false} projectId="background" />);
    await act(async () => {});
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(String(fetchMock.mock.calls[0][0])).toContain("project_id=background");
  });

  it("discards an old project's late task response after switching projects", async () => {
    let finishOld!: (response: Response) => void;
    let oldSignal!: AbortSignal;
    const fetchMock = vi.fn((url: string, init: RequestInit) => {
      if (url.includes("project_id=first")) {
        oldSignal = init.signal as AbortSignal;
        return new Promise<Response>((resolve) => { finishOld = resolve; });
      }
      return Promise.resolve(new Response("[]"));
    });
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<GlobalRequestStatus locallyBusy={false} projectId="first" />);
    await act(async () => {});
    view.rerender(<GlobalRequestStatus locallyBusy={false} projectId="second" />);
    expect(oldSignal.aborted).toBe(true);
    await act(async () => {
      finishOld(new Response(JSON.stringify([{ task_id: "old-task", kind: "export_txt", status: "completed", unread: true, progress: 100 }])));
    });
    fireEvent.click(screen.getByRole("button", { name: "任务中心" }));
    expect(screen.getByText("当前没有本地长任务。")).toBeVisible();
    expect(screen.queryByText("导出 TXT")).not.toBeInTheDocument();
  });

  it("tracks concurrent API requests until the count returns to zero", () => {
    render(<GlobalRequestStatus locallyBusy={false} />);

    act(() => window.dispatchEvent(new CustomEvent("novel-writer:pending", { detail: 2 })));
    expect(screen.getByRole("status")).toBeInTheDocument();

    act(() => window.dispatchEvent(new CustomEvent("novel-writer:pending", { detail: 1 })));
    expect(screen.getByRole("status")).toBeInTheDocument();

    act(() => window.dispatchEvent(new CustomEvent("novel-writer:pending", { detail: 0 })));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("allows hiding a long-running notice without cancelling the request", () => {
    vi.useFakeTimers();
    render(<GlobalRequestStatus locallyBusy />);

    act(() => vi.advanceTimersByTime(15_000));
    fireEvent.click(screen.getByRole("button", { name: "隐藏后台提示" }));

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("lists each remote novel with its current stage", () => {
    render(<GlobalRequestStatus locallyBusy={false} />);
    const operations: ProjectOperation[] = ["示例作品甲", "示例作品乙"].map((projectTitle, id) => ({
      id, projectId: String(id), projectTitle, stage: id ? "Memory 记忆接力阶段" : "Writer 正文续写阶段",
      startedAt: Date.now(), updatedAt: Date.now(), status: "running", source: "backend", detail: null,
    }));
    act(() => receiveRemoteProjectOperations("test-tab", operations));
    expect(screen.getByRole("status")).toHaveTextContent("2 部小说正在独立处理");
    expect(screen.getByRole("status")).toHaveTextContent("《示例作品甲》现在正在进行 Writer 正文续写阶段");
    expect(screen.getByRole("status")).toHaveTextContent("《示例作品乙》现在正在进行 Memory 记忆接力阶段");
    act(() => receiveRemoteProjectOperations("test-tab", []));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("shows a queued operation received from another tab until it finishes", () => {
    render(<GlobalRequestStatus locallyBusy={false} />);
    act(() => receiveRemoteProjectOperations("test-tab", [{
      id: 1, projectId: "queued-project", projectTitle: "排队作品", stage: "Writer 正文续写阶段",
      startedAt: Date.now(), updatedAt: Date.now(), status: "queued", source: "backend", detail: null,
    }]));
    expect(screen.getByRole("status")).toHaveTextContent("已入队，等待后台执行");
    act(() => receiveRemoteProjectOperations("test-tab", []));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
