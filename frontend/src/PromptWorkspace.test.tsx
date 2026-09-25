import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PromptWorkspace } from "./PromptWorkspace";
import { getDirtySurfaces } from "./unsavedChanges";

const mocks = vi.hoisted(() => ({ api: vi.fn(), confirm: vi.fn() }));
vi.mock("./api", () => ({
  api: mocks.api, commandHeaders: () => ({}), jsonBody: JSON.stringify,
  errorMessage: (value: Error) => value.message,
}));
vi.mock("./confirmation", () => ({ requestConfirmation: mocks.confirm }));

const text = { system_text: "完整默认系统文本", task_template: "作者任务\n{{story_task}}" };
function catalog(revision = "builtin", saved = text, customized = false) {
  return {
    revision, engine_system: "来源保护", entries: ["chief", "writer"].map((variant) => ({
      variant, label: variant === "chief" ? "Chief 剧情设计" : "Writer 单元写作",
      text: saved, default_text: text, customized,
      fields: [{ key: "story_task", label: "作者任务", required: true }], output_requirement: "输出合同",
    })), history: [{ revision: "prior", created_at: "2026-09-25T08:00:00Z", note: "原版本" }],
  };
}

beforeEach(() => {
  mocks.api.mockReset(); mocks.confirm.mockReset(); mocks.confirm.mockResolvedValue(false);
  mocks.api.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url.endsWith("generation-batches")) return [{ id: "batch-1", direction: "测试故事", created_at: "2026-09-25T08:00:00Z" }];
    if (options?.method === "PUT") return catalog("saved", JSON.parse(options.body as string).template ?? text, !!JSON.parse(options.body as string).template);
    if (url.includes("/versions/")) return { text: { ...text, system_text: "历史系统文本" }, customized: true, revision: "prior" };
    if (url.endsWith("/preview")) return { system_prompt: "最终系统", task_prompt: "最终任务", input_count: 1234,
      counting_method: "utf8-byte-upper-bound", blockers: [], omitted_optional: [], engine_contract: {},
      source_action: "plan", source_description: "使用真实冻结资料" };
    return catalog();
  });
});
afterEach(cleanup);

async function mount() {
  await act(async () => { render(<PromptWorkspace projectId="book-1" />); });
  await screen.findByLabelText("系统 Prompt 默认文本");
}

describe("Prompt 模板编辑", () => {
  it("loads full defaults, saves replacements with a revision, and registers unsaved edits", async () => {
    await mount();
    expect(screen.getByLabelText("系统 Prompt 默认文本")).toHaveValue(text.system_text);
    fireEvent.change(screen.getByLabelText("系统 Prompt 默认文本"), { target: { value: "作者替换的文本" } });
    expect(getDirtySurfaces().some((surface) => surface.key === "prompt-templates")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "保存为默认" }));
    await screen.findByText(/已保存。新建预览/);
    const call = mocks.api.mock.calls.find(([, options]) => options?.method === "PUT")!;
    expect(JSON.parse(call[1].body)).toMatchObject({ expected_revision: "builtin", template: { system_text: "作者替换的文本" } });
    expect(getDirtySurfaces()).toHaveLength(0);
    expect(mocks.api.mock.calls.some(([path]) => /authorize|continue|recover/.test(path))).toBe(false);
  });

  it("previews the unsaved draft without saving or authorizing a call", async () => {
    await mount();
    fireEvent.change(screen.getByLabelText("任务 Prompt 模板"), { target: { value: "自定义标题\n{{story_task}}" } });
    fireEvent.click(screen.getByRole("button", { name: "预览当前草稿（不调用模型）" }));
    await screen.findByText("最终任务");
    expect(screen.getByText(/并非实际 tokens/)).toBeInTheDocument();
    const calls = mocks.api.mock.calls.filter(([, options]) => options?.method);
    expect(calls).toHaveLength(1);
    expect(JSON.parse(calls[0][1].body)).toMatchObject({ project_id: "book-1", batch_id: "batch-1", variant: "chief", template: { task_template: "自定义标题\n{{story_task}}" } });
    fireEvent.change(screen.getByLabelText("系统 Prompt 默认文本"), { target: { value: "修改后" } });
    expect(screen.queryByText("最终任务")).not.toBeInTheDocument();
  });

  it("retains edits on conflict and blocks discarded role switches", async () => {
    await mount();
    fireEvent.change(screen.getByLabelText("系统 Prompt 默认文本"), { target: { value: "未保存" } });
    fireEvent.change(screen.getByLabelText("角色与任务"), { target: { value: "writer" } });
    await waitFor(() => expect(mocks.confirm).toHaveBeenCalled());
    expect(screen.getByLabelText("角色与任务")).toHaveValue("chief");
    mocks.api.mockRejectedValueOnce(new Error("默认模板已在其他页面更新"));
    fireEvent.click(screen.getByRole("button", { name: "保存为默认" }));
    await screen.findByRole("alert");
    expect(screen.getByLabelText("系统 Prompt 默认文本")).toHaveValue("未保存");
    expect(getDirtySurfaces()).toHaveLength(1);
  });

  it("compares history and restores it only as a draft", async () => {
    await mount();
    fireEvent.change(screen.getByLabelText("已保存版本"), { target: { value: "prior" } });
    await screen.findByRole("button", { name: "将此版本载入草稿" });
    expect(screen.getByLabelText("系统 Prompt 默认文本")).toHaveValue(text.system_text);
    fireEvent.click(screen.getByRole("button", { name: "将此版本载入草稿" }));
    expect(screen.getByLabelText("系统 Prompt 默认文本")).toHaveValue("历史系统文本");
    expect(mocks.api.mock.calls.some(([, options]) => options?.method === "PUT")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "恢复项目默认到草稿" }));
    expect(screen.getByLabelText("系统 Prompt 默认文本")).toHaveValue(text.system_text);
  });

  it("does not render user template text as HTML", async () => {
    await mount();
    fireEvent.change(screen.getByLabelText("系统 Prompt 默认文本"), { target: { value: "<script>bad()</script>" } });
    expect(document.querySelector("script")).toBeNull();
  });

  it("explains duplicate placeholders beside Save, selects the extra occurrence, and saves after correction", async () => {
    await mount();
    const original = "我的任务结构\n{{story_task}}";
    const duplicate = original + "\n{{ story_task }}";
    fireEvent.change(screen.getByLabelText("任务 Prompt 模板"), { target: { value: duplicate } });
    fireEvent.click(screen.getByRole("button", { name: "保存为默认" }));
    const feedback = screen.getByLabelText("模板保存反馈");
    await within(feedback).findByText(/尚未保存/);
    expect(within(feedback).getByText(/重复出现 2 次/)).toBeInTheDocument();
    expect(mocks.api.mock.calls.some(([, options]) => options?.method === "PUT")).toBe(false);
    const textarea = screen.getByLabelText<HTMLTextAreaElement>("任务 Prompt 模板");
    expect(textarea).toHaveValue(duplicate);
    fireEvent.click(within(feedback).getByRole("button", { name: "定位问题字段 story_task" }));
    expect(textarea).toHaveFocus();
    expect(duplicate.slice(textarea.selectionStart, textarea.selectionEnd)).toBe("{{ story_task }}");
    fireEvent.change(textarea, { target: { value: original } });
    expect(screen.queryByText(/重复出现/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存为默认" }));
    await within(feedback).findByText(/已保存。新建预览/);
    expect(textarea).toHaveValue(original);
  });

  it("locates an existing placeholder instead of inserting a duplicate", async () => {
    await mount();
    fireEvent.click(screen.getByRole("button", { name: "作者任务（必要）" }));
    const textarea = screen.getByLabelText<HTMLTextAreaElement>("任务 Prompt 模板");
    expect(textarea).toHaveValue(text.task_template);
    expect(textarea.value.slice(textarea.selectionStart, textarea.selectionEnd)).toBe("{{story_task}}");
    expect(screen.getByRole("button", { name: "保存为默认" })).toBeDisabled();
    expect(screen.getByText(/没有重复插入/)).toBeInTheDocument();
  });

  it("shows backend rejection beside the button and keeps all draft text", async () => {
    await mount();
    fireEvent.change(screen.getByLabelText("系统 Prompt 默认文本"), { target: { value: "完整作者修改" } });
    let reject!: (reason: Error) => void;
    mocks.api.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail; }));
    fireEvent.click(screen.getByRole("button", { name: "保存为默认" }));
    const feedback = screen.getByLabelText("模板保存反馈");
    expect(within(feedback).getByText("正在处理，请稍候…")).toBeInTheDocument();
    await act(async () => { reject(new Error("后端拒绝了当前模板")); });
    expect(within(feedback).getByRole("alert")).toHaveTextContent("后端拒绝了当前模板");
    expect(screen.getByLabelText("系统 Prompt 默认文本")).toHaveValue("完整作者修改");
    expect(screen.getByRole("button", { name: "保存为默认" })).toBeEnabled();
  });
});
