import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ReaderApp } from "./ReaderApp";

const chapters = [
  { chapter_id: "c1", revision_id: "r1", ordinal: 1, title: "启程", char_count: 10 },
  { chapter_id: "c2", revision_id: "r2", ordinal: 2, title: "归来", char_count: 10 },
];
beforeEach(() => {
  history.replaceState(null, "", "/");
  vi.stubGlobal("fetch", vi.fn(async (path: string) => {
    const payload = path.endsWith("/api/projects") ? [{ id: "book", title: "测试长篇", formal_version: 1, chapter_count: 2 }]
      : path.includes("/manifest") ? { project_id: "book", title: "测试长篇", version_id: "v1", formal_version: 1, chapters }
        : path.includes("/search?") ? { results: [{ ...chapters[1], snippet: "雨后归来。" }], has_more: false }
          : { ...chapters[path.includes("/c2?") ? 1 : 0], body: path.includes("/c2?") ? "雨后归来。" : "晨光中出发。" };
    return { ok: true, json: async () => payload };
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("reads formal chapters with version-bound GETs and no browser persistence", async () => {
  const storage = vi.spyOn(Storage.prototype, "setItem");
  render(<ReaderApp />);
  fireEvent.click(await screen.findByRole("button", { name: /测试长篇/ }));
  await screen.findByText("晨光中出发。");
  fireEvent.click(screen.getByRole("button", { name: "下一章 →" }));
  await screen.findByText("雨后归来。");
  expect(screen.getByRole("button", { name: "下一章 →" })).toBeDisabled();
  expect(vi.mocked(fetch).mock.calls.every(([path, init]) => String(path).startsWith("/backend/api/projects") && !init?.method)).toBe(true);
  expect(vi.mocked(fetch).mock.calls.some(([path]) => String(path).includes("version_id=v1"))).toBe(true);
  expect(storage).not.toHaveBeenCalled();
  expect(screen.queryByText("开始创作")).toBeNull();
  expect(screen.queryByText("模型配置")).toBeNull();
});

it("searches only this book and opens the matching chapter", async () => {
  render(<ReaderApp />);
  fireEvent.click(await screen.findByRole("button", { name: /测试长篇/ }));
  await screen.findByText("晨光中出发。");
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索本书" }), { target: { value: "雨后" } });
  fireEvent.click(await screen.findByRole("button", { name: /归来.*雨后归来/ }));
  await waitFor(() => expect(document.querySelector("mark")).toHaveTextContent("雨后"));
});

it("shows version conflict without silently substituting another chapter", async () => {
  const initial = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (...args) => String(args[0]).includes("/chapters/")
    ? { ok: false, status: 409, json: async () => ({ detail: "正式版本已更新，请刷新目录后继续阅读" }) } as Response
    : initial(...args));
  render(<ReaderApp />);
  fireEvent.click(await screen.findByRole("button", { name: /测试长篇/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent("正式版本已更新");
  expect(screen.queryByText("晨光中出发。")).toBeNull();
});
