import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { KnowledgePanel, SavedContextSelection, SavedKnowledge } from "./KnowledgePanel";

const mocks = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock("./api", () => ({ api: mocks.api, jsonBody: JSON.stringify, errorMessage: String, isAbortError: () => false }));
const hit = { id: "piece", kind: "chapter", text: "她交出了那把剑。", ordinal: 2, start: 0, end: 9, source_id: "revision" };

beforeEach(() => mocks.api.mockReset());
afterEach(cleanup);
function open() {
  const details = screen.getByText("本书知识库与检索").closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
}

describe("本书知识库", () => {
  it("shows protected Chief continuity and distinguishes byte bounds from tokens", () => {
    render(<SavedContextSelection selection={{ policy: "chief-focus-v4", material_count: 13000, soft_target: 12000, counting_method: "utf8-byte-upper-bound", required_above_target: true, protected_continuity: { recent_summaries: 1, history_paragraphs: 1 } }} />);
    expect(screen.getByText(/优先保留 1 份近期摘要、1 段相关历史原文/)).toBeTruthy();
    expect(screen.getByText(/UTF-8 字节（保守上界，非实际 tokens）/)).toBeTruthy();
    expect(screen.getByText(/最终请求仍受授权容量约束/)).toBeTruthy();
    expect(mocks.api).not.toHaveBeenCalled();
  });

  it("loads on demand and searches only the selected novel", async () => {
    mocks.api.mockResolvedValueOnce({ status: "lexical", mode: "lexical", chunk_count: 2 });
    render(<KnowledgePanel projectId="novel-a" version={1} />);
    expect(mocks.api).not.toHaveBeenCalled();
    open();
    await screen.findByText(/文本检索可用/);
    mocks.api.mockResolvedValueOnce({ mode: "lexical", version_id: "v1", hits: [hit] });
    fireEvent.change(screen.getByLabelText("本书知识检索词"), { target: { value: "交剑" } });
    fireEvent.click(screen.getByText("搜索本书资料"));
    await screen.findByText("她交出了那把剑。");
    expect(mocks.api).toHaveBeenLastCalledWith("/api/projects/novel-a/knowledge/search", { method: "POST", body: JSON.stringify({ query: "交剑" }) });
  });

  it("does not display an old search after the formal version changes", async () => {
    mocks.api.mockResolvedValue({ status: "ready", chunk_count: 1 });
    const view = render(<KnowledgePanel projectId="novel-a" version={1} />);
    open();
    await screen.findByText(/语义索引已就绪/);
    let finish!: (value: unknown) => void;
    mocks.api.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    fireEvent.change(screen.getByLabelText("本书知识检索词"), { target: { value: "交剑" } });
    fireEvent.click(screen.getByText("搜索本书资料"));
    await waitFor(() => expect(finish).toBeDefined());
    view.rerender(<KnowledgePanel projectId="novel-a" version={2} />);
    await act(async () => finish({ mode: "hybrid", hits: [hit] }));
    expect(screen.queryByText("她交出了那把剑。")).toBeNull();
  });

  it("shows only excerpts in the saved actual prompt without issuing requests", () => {
    const view = render(<SavedKnowledge userPrompt={JSON.stringify({ knowledge_context: { hits: [hit] } })} />);
    expect(screen.getByText("她交出了那把剑。")).toBeTruthy();
    expect(mocks.api).not.toHaveBeenCalled();
    view.rerender(<SavedKnowledge userPrompt="旧版非 JSON Prompt" />);
    expect(screen.queryByText("她交出了那把剑。")).toBeNull();
  });
});
