import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReaderView } from "./ReaderView";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
});

describe("ReaderView copy actions", () => {
  it("copies the current chapter or the whole formal collection with readable formatting", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      json: async () => ({
        project_id: "project-1",
        title: "示例作品",
        formal_version: 21,
        chapters: [
          { chapter_id: "chapter-1", revision_id: "revision-1", ordinal: 1, title: "旧城", body: "第一段。\n第二段。", body_sha256: "a".repeat(64) },
          { chapter_id: "chapter-2", revision_id: "revision-2", ordinal: 2, title: "新局", body: "第三段。", body_sha256: "b".repeat(64) },
        ],
      }),
    }) as Response));

    render(<ReaderView projectId="project-1" />);
    await screen.findByRole("heading", { name: "旧城" });

    fireEvent.click(screen.getByRole("button", { name: "复制本章" }));
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(
      "旧城\n\n　　第一段。\n\n　　第二段。",
    ));
    expect(screen.getByRole("status")).toHaveTextContent("第 1 章已复制");

    fireEvent.click(screen.getByRole("button", { name: "复制全部章节" }));
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(
      "旧城\n\n　　第一段。\n\n　　第二段。\n\n\n新局\n\n　　第三段。",
    ));
    expect(screen.getByRole("status")).toHaveTextContent("全部 2 章已复制");
  });

  it("windows a 500 chapter directory and fetches only the current and adjacent bodies", async () => {
    const chapters = Array.from({ length: 500 }, (_, index) => ({
      chapter_id: `chapter-${index + 1}`,
      revision_id: `revision-${index + 1}`,
      ordinal: index + 1,
      title: `章节 ${index + 1}`,
      char_count: 5000,
      body_sha256: String(index + 1).padStart(64, "0"),
    }));
    const bodyRequests: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/reader/manifest")) {
        return { ok: true, json: async () => ({
          project_id: "project-1", title: "长篇", formal_version: 3, chapters,
        }) } as Response;
      }
      if (url.endsWith("/bookmarks")) {
        return { ok: true, json: async () => [] } as Response;
      }
      const chapterId = url.split("/").at(-1) ?? "";
      bodyRequests.push(chapterId);
      const chapter = chapters.find((item) => item.chapter_id === chapterId)!;
      return { ok: true, json: async () => ({
        project_id: "project-1", formal_version: 3, ...chapter, body: `正文 ${chapter.ordinal}`,
      }) } as Response;
    }));

    render(<ReaderView projectId="project-1" />);

    await screen.findByRole("heading", { name: "章节 1" });
    await waitFor(() => expect(bodyRequests).toEqual(["chapter-1", "chapter-2"]));
    expect(screen.getAllByRole("button", { name: /章节 \d+/u }).length).toBeLessThan(40);
    expect(screen.getByRole("navigation", { name: "章节目录，共 500 章" })).toBeInTheDocument();
  });

  it("uses a SHA-bound search target once and highlights the matching formal body", async () => {
    window.sessionStorage.setItem("novel-writer:search-target-v1", JSON.stringify({
      project_id: "project-1",
      source_kind: "chapter",
      source_id: "revision-2",
      source_sha256: "b".repeat(64),
      chapter_id: "chapter-2",
      offset: 4,
      match_field: "text",
      query: "潮印",
    }));
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/reader/manifest")) return { ok: true, json: async () => ({
        project_id: "project-1",
        title: "长篇",
        formal_version: 3,
        chapters: [
          { chapter_id: "chapter-1", revision_id: "revision-1", ordinal: 1, title: "旧城", char_count: 10, body_sha256: "a".repeat(64) },
          { chapter_id: "chapter-2", revision_id: "revision-2", ordinal: 2, title: "潮声", char_count: 12, body_sha256: "b".repeat(64) },
        ],
      }) } as Response;
      if (url.endsWith("/bookmarks")) return { ok: true, json: async () => [] } as Response;
      const chapterId = url.split("/").at(-1);
      return { ok: true, json: async () => ({
        project_id: "project-1",
        formal_version: 3,
        chapter_id: chapterId,
        revision_id: chapterId === "chapter-2" ? "revision-2" : "revision-1",
        ordinal: chapterId === "chapter-2" ? 2 : 1,
        title: chapterId === "chapter-2" ? "潮声" : "旧城",
        body: chapterId === "chapter-2" ? "林遥看见潮印熄灭。" : "旧城无声。",
        body_sha256: chapterId === "chapter-2" ? "b".repeat(64) : "a".repeat(64),
      }) } as Response;
    }));

    render(<ReaderView projectId="project-1" />);

    expect(await screen.findByRole("heading", { name: "潮声" })).toBeInTheDocument();
    expect(await screen.findByText("潮印", { selector: "mark" })).toBeInTheDocument();
    expect(window.sessionStorage.getItem("novel-writer:search-target-v1")).toBeNull();
  });
});
