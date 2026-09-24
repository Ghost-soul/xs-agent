import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { clearDirtySurface, setDirtySurface } from "./unsavedChanges";

vi.mock("./BlueprintWorkspace", () => ({ BlueprintWorkspace: ({ onSaved }: { onSaved: () => Promise<void> }) => <><h2>手工故事资料</h2><button onClick={() => void onSaved()}>测试保存回调</button></> }));

beforeEach(() => {
  window.history.replaceState({}, "", "/projects/project-nav/home");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    expect(init?.method ?? "GET").toBe("GET");
    const path = String(input);
    let payload: unknown = [];
    if (path.endsWith("/health")) payload = { status: "ok" };
    else if (path.endsWith("/api/projects")) payload = [{
      project_id: "project-nav", title: "管理测试小说", current_version: 1,
      created_at: "2026-09-21T00:00:00Z",
    }];
    else if (path.endsWith("/api/search/status")) payload = { status: "ready" };
    else if (!["/api/projects/archived", "/chapters", "/versions", "/api/local-tasks"].some((suffix) => path.endsWith(suffix))) {
      throw new Error(`Unexpected request: ${path}`);
    }
    return new Response(JSON.stringify(payload), { status: 200 });
  }));
});

afterEach(() => {
  cleanup();
  clearDirtySurface("navigation-test");
  vi.unstubAllGlobals();
});

describe("management navigation", () => {
  it("refreshes saved project lists only once and starts independent reads together", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "当前作品" });
    fireEvent.click(screen.getByRole("button", { name: "故事资料" }));
    const button = await screen.findByText("测试保存回调");
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockClear();
    fireEvent.click(button);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    expect(fetchMock.mock.calls.map(([input]) => String(input)).sort()).toEqual([
      "/backend/api/projects", "/backend/api/projects/archived",
      "/backend/api/projects/project-nav/chapters", "/backend/api/projects/project-nav/versions",
    ]);
  });

  it("shows the new creation entry and retains management navigation", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "当前作品" });
    expect(screen.getByRole("button", { name: "题材主导创作" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "继续创作" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "审阅候选" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "故事资料" }));
    expect(await screen.findByRole("heading", { name: "手工故事资料" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "工作台" }));
    expect(await screen.findByRole("heading", { name: "当前作品" })).toBeVisible();
  });

  it("opens retired generation links without generation requests", async () => {
    window.history.replaceState({}, "", "/projects/project-nav/write?run=old-run");
    render(<App />);
    await screen.findByRole("heading", { name: "当前作品" });
    await waitFor(() => expect(window.location.pathname).toBe("/projects/project-nav/home"));
    expect(window.location.search).toBe("");
  });

  it("protects unsaved management edits during navigation", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "当前作品" });
    act(() => setDirtySurface("navigation-test", "测试未保存资料", true));
    fireEvent.click(screen.getByRole("button", { name: "故事资料" }));
    expect(await screen.findByRole("dialog")).toBeVisible();
    expect(window.location.pathname).toBe("/projects/project-nav/home");
  });
});
