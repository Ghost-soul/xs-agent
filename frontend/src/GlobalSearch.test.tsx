import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GlobalSearch } from "./GlobalSearch";

afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
  window.history.replaceState({}, "", "/");
  vi.unstubAllGlobals();
});

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("GlobalSearch", () => {
  it("uses the local index API, exposes stale rebuild, and opens a stable exact route", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const onNavigate = vi.fn((href: string) => window.history.pushState({}, "", href));
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url.includes("/backend/api/search/status?")) {
        return response({ status: "stale" });
      }
      if (url.includes("/backend/api/search?")) {
        return response({
          query: "潮印",
          scope: "all",
          include_history: false,
          results: [{
            source_kind: "chapter",
            source_id: "revision-1",
            title: "潮声",
            snippet: "林遥看见潮印熄灭。",
            offset: 4,
            match_field: "text",
            version_id: "version-1",
            version: 1,
            revision_id: "revision-1",
            source_sha256: "a".repeat(64),
            route: "/projects/project-1/read?chapter=chapter-1",
            chapter_id: "chapter-1",
            locator: {
              route: "/projects/project-1/read?chapter=chapter-1",
              chapter_id: "chapter-1",
              resource_id: "chapter-1",
              session_id: null,
              section: "chapter",
              ordinal: 1,
            },
            historical: false,
            source_scope: "current",
          }],
          next_cursor: null,
          total: 1,
          provider_called: false,
          index_status: "stale",
        });
      }
      if (url.endsWith("/backend/api/search/rebuild")) {
        return response({
          task_id: "task-1",
          project_id: "project-1",
          kind: "search_rebuild",
          status: "queued",
          input_sha256: "b".repeat(64),
          provider_called: false,
        }, 202);
      }
      throw new Error(`unexpected request: ${url}`);
    }));

    render(<GlobalSearch projectId="project-1" onNavigate={onNavigate} />);
    fireEvent.change(screen.getByLabelText("搜索正文和故事资料"), { target: { value: "潮印" } });

    const result = await screen.findByRole("option");
    expect(requests.some((item) => item.url.includes("/backend/api/search?project_id=project-1"))).toBe(true);
    await waitFor(() => expect(requests.some((item) => item.url.endsWith("/backend/api/search/rebuild"))).toBe(true));

    fireEvent.click(result);

    expect(onNavigate).toHaveBeenCalledWith("/projects/project-1/read?chapter=chapter-1");
    expect(window.location.pathname).toBe("/projects/project-1/read");
    expect(window.location.search).toBe("?chapter=chapter-1");
    expect(JSON.parse(window.sessionStorage.getItem("novel-writer:search-target-v1") ?? "null")).toMatchObject({
      project_id: "project-1",
      source_sha256: "a".repeat(64),
      chapter_id: "chapter-1",
      offset: 4,
      query: "潮印",
    });
    expect(requests.some((item) => /provider-next-action|run-to-review|execute/u.test(item.url))).toBe(false);
  });
});
