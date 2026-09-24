import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, type GenerationDetail } from "./api";
import { GenerationCallLog } from "./GenerationCallLog";
import { actionName } from "./GenerationProgress";
import { copyPlainText } from "./novelText";

vi.mock("./api", async (original) => ({ ...await original<typeof import("./api")>(), api: vi.fn() }));
vi.mock("./novelText", () => ({ copyPlainText: vi.fn().mockResolvedValue(undefined) }));
const base = "/api/projects/p/generation-batches";
const calls = ["plan", "write:1", "memory:1", "checker", "reader", "amend"].map((action, i) => ({
  id: `call-${i}`, action, status: i === 2 ? "local_failure" : "completed", input_tokens: 45000,
  started_at: `2026-09-23T07:0${i}:00Z`, actual_cost_cny: "0.1", can_revalidate: false,
}));
const batch = { id: "batch", status: "needs_attention", spec: {}, state: {}, snapshot: { maximum_calls: 6 }, calls, artifacts: [] } as unknown as GenerationDetail;
const receipt = {
  status: "completed", request: { model_request: { model: "saved-model", system_prompt: "当时的系统要求\n第二行", user_prompt: '{"方向":"当时的方向","context":"原始上下文"}' }, wire_body: '{"messages": ["原始协议"]}' },
  response: { text: '{"原始输出":"不重新编译"}', raw_response: 'data: {"供应商":"原始响应"}\n\ndata: [DONE]\n', terminal: { finish_reason: "stop", terminal_status: "completed" } },
};
const onRevalidate = vi.fn().mockResolvedValue(undefined);
function show(value = batch) {
  return render(<GenerationCallLog key={`${base}/${value.id}`} base={base} batch={value} busy={false} onRevalidate={onRevalidate} />);
}
function open(action = "plan") { fireEvent.click(screen.getByRole("button", { name: `${actionName(action)}：查看 Prompt 与输出` })); }
function tab(name: string) { fireEvent.click(screen.getByRole("tab", { name })); }

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
beforeEach(() => { vi.clearAllMocks(); vi.mocked(api).mockResolvedValue(receipt); });

describe("original agent call records", () => {
  it("shows the actual input and output together and can copy either without writes", async () => {
    show(); open("write:1");
    expect((await screen.findByLabelText("系统 Prompt", { selector: "pre" })).textContent).toBe(receipt.request.model_request.system_prompt);
    expect(screen.getByLabelText("任务 Prompt", { selector: "pre" }).textContent).toBe(receipt.request.model_request.user_prompt);
    expect(screen.getByLabelText("模型原始输出", { selector: "pre" }).textContent).toBe(receipt.response.text);
    fireEvent.click(screen.getByRole("button", { name: "复制任务 Prompt" }));
    await screen.findByText("已复制任务 Prompt原文。");
    expect(copyPlainText).toHaveBeenLastCalledWith(receipt.request.model_request.user_prompt);
    fireEvent.click(screen.getByRole("button", { name: "复制模型原始输出" }));
    await screen.findByText("已复制模型原始输出原文。");
    expect(copyPlainText).toHaveBeenLastCalledWith(receipt.response.text);
    expect(api).toHaveBeenCalledOnce();
    expect(onRevalidate).not.toHaveBeenCalled();
  });

  it("opens the latest call for a role, keeps earlier units accessible and includes revision actions", async () => {
    show({ ...batch, calls: [...calls, { ...calls[2], id: "memory-new", action: "memory_amend", started_at: "2026-09-23T08:00:00Z" }] });
    fireEvent.click(screen.getByRole("button", { name: "Memory（2）" }));
    await screen.findByLabelText("任务 Prompt", { selector: "pre" });
    expect(api).toHaveBeenLastCalledWith(`${base}/batch/calls/memory-new`, expect.anything());
    expect(screen.queryByRole("button", { name: "Chief 设计剧情：查看 Prompt 与输出" })).toBeNull();
    open("memory:1");
    await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
    expect(api).toHaveBeenLastCalledWith(`${base}/batch/calls/call-2`, expect.anything());
    fireEvent.click(screen.getByRole("button", { name: "Editor（1）" }));
    await screen.findByLabelText("任务 Prompt", { selector: "pre" });
    expect(api).toHaveBeenLastCalledWith(`${base}/batch/calls/call-5`, expect.anything());
    expect(onRevalidate).not.toHaveBeenCalled();
  });

  it("shows uncalled roles without fabricating prompts or loading any receipt", () => {
    show({ ...batch, calls: [calls[0]] });
    fireEvent.click(screen.getByRole("button", { name: "Reader（0）" }));
    expect(screen.getByText("Reader 在本批次尚无调用记录，执行后才会有实际输入与输出。")).toBeVisible();
    expect(api).not.toHaveBeenCalled();
    expect(screen.queryByRole("tab", { name: "输入与输出" })).toBeNull();
  });
  it("loads each role only when clicked, including the saved failed Memory response", async () => {
    show();
    expect(api).not.toHaveBeenCalled();
    for (const call of calls) {
      open(call.action);
      expect((await screen.findByLabelText("系统 Prompt", { selector: "pre" })).textContent).toBe(receipt.request.model_request.system_prompt);
      expect(api).toHaveBeenLastCalledWith(`${base}/batch/calls/${call.id}`, expect.objectContaining({ signal: expect.any(AbortSignal) }));
      tab("模型原始输出");
      expect(screen.getByLabelText("模型原始输出", { selector: "pre" }).textContent).toBe(receipt.response.text);
    }
    expect(api).toHaveBeenCalledTimes(6);
    expect(onRevalidate).not.toHaveBeenCalled();
    expect(vi.mocked(api).mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });

  it("preserves long prompts, whitespace, raw responses and literal markup when reading and copying", async () => {
    const prompt = '首行\r\n  中文 <script>禁止执行</script>\n' + "很长的上下文 ".repeat(25000) + "\n末尾原文";
    vi.mocked(api).mockResolvedValue({ ...receipt, request: { ...receipt.request, model_request: { ...receipt.request.model_request, user_prompt: prompt } } });
    show(); open();
    await screen.findByLabelText("系统 Prompt", { selector: "pre" });
    tab("任务 Prompt");
    expect(screen.getByLabelText("任务 Prompt", { selector: "pre" }).textContent).toBe(prompt);
    expect(document.querySelector("script")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "复制原文" }));
    await screen.findByText("已复制任务 Prompt原文。");
    expect(copyPlainText).toHaveBeenCalledWith(prompt);
    tab("实际请求体");
    expect(screen.getByLabelText("实际请求体", { selector: "pre" }).textContent).toBe(receipt.request.wire_body);
    tab("供应商原始响应");
    expect(screen.getByLabelText("供应商原始响应", { selector: "pre" }).textContent).toBe(receipt.response.raw_response);
    fireEvent.keyDown(screen.getByRole("tab", { name: "供应商原始响应" }), { key: "Home" });
    expect(screen.getByRole("tab", { name: "输入与输出" })).toHaveFocus();
  });

  it("downloads the selected original text and the complete receipt with its call identity", async () => {
    const blobs: BlobPart[][] = [];
    const RealBlob = Blob;
    vi.stubGlobal("Blob", class extends RealBlob { constructor(parts: BlobPart[], options?: BlobPropertyBag) { super(parts, options); blobs.push(parts); } });
    vi.stubGlobal("URL", { createObjectURL: vi.fn().mockReturnValue("blob:receipt"), revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    show(); open("memory:1");
    await screen.findByLabelText("系统 Prompt", { selector: "pre" });
    tab("模型原始输出");
    fireEvent.click(screen.getByRole("button", { name: "下载原文" }));
    expect(blobs[0]).toEqual([receipt.response.text]);
    expect((click.mock.instances[0] as HTMLAnchorElement).download).toBe("memory-1-call-2-output.txt");
    fireEvent.click(screen.getByRole("button", { name: "下载完整调用记录" }));
    expect(JSON.parse(String(blobs[1][0]))).toEqual({ batch_id: "batch", call_id: "call-2", action: "memory:1", ...receipt });
  });

  it("cancels a slow old selection and never shows it under another agent or batch", async () => {
    let resolveOld!: (value: unknown) => void;
    vi.mocked(api).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    const view = show(); open();
    const signal = vi.mocked(api).mock.calls[0][1]!.signal!;
    open("write:1");
    await screen.findByLabelText("系统 Prompt", { selector: "pre" });
    expect(signal.aborted).toBe(true);
    resolveOld({ ...receipt, request: { model_request: { system_prompt: "过时响应不应出现" } } });
    await waitFor(() => expect(screen.queryByText("过时响应不应出现")).toBeNull());
    view.rerender(<GenerationCallLog key="other" base={base} batch={{ ...batch, id: "other" }} busy={false} onRevalidate={onRevalidate} />);
    expect(screen.queryByRole("tab", { name: "系统 Prompt" })).toBeNull();
    open();
    await screen.findByLabelText("系统 Prompt", { selector: "pre" });
    expect(api).toHaveBeenLastCalledWith(`${base}/other/calls/call-0`, expect.anything());
  });

  it("distinguishes an absent response, an empty output and a truncated saved output", async () => {
    vi.mocked(api).mockResolvedValueOnce({ ...receipt, response: null });
    show(); open();
    await screen.findByText(/尚未保存模型响应/);
    tab("模型原始输出");
    expect(screen.getByText("此记录未保存模型原始输出。")).toBeTruthy();
    expect(screen.getByRole("button", { name: "复制原文" })).toBeDisabled();
    vi.mocked(api).mockResolvedValueOnce({ ...receipt, response: { text: "", terminal: { finish_reason: "length" } } });
    fireEvent.click(screen.getByRole("button", { name: "刷新记录" }));
    await screen.findByText("已保存的模型原始输出为空。");
    expect(screen.getByRole("alert")).toHaveTextContent("该响应未完整结束");
    tab("供应商原始响应");
    expect(screen.getByText("此记录未保存供应商原始响应。")).toBeTruthy();
  });

  it("refreshes an open running call when its status changes to a saved result", async () => {
    const running = { ...batch, calls: [{ ...calls[0], status: "executing" }] };
    vi.mocked(api).mockResolvedValueOnce({ ...receipt, status: "executing", response: null });
    const view = show(running); open();
    await screen.findByText(/尚未保存模型响应/);
    view.rerender(<GenerationCallLog key={`${base}/${batch.id}`} base={base} batch={{ ...batch, calls: [calls[0]] }} busy={false} onRevalidate={onRevalidate} />);
    await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
    await screen.findByLabelText("系统 Prompt", { selector: "pre" });
    tab("模型原始输出");
    expect(screen.getByLabelText("模型原始输出", { selector: "pre" }).textContent).toBe(receipt.response.text);
  });

  it("lets the user retry failed reads without authorizing or revalidating a call", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("读取中断"));
    show(); open();
    expect(await screen.findByRole("alert")).toHaveTextContent("读取中断");
    fireEvent.click(screen.getByRole("button", { name: "刷新记录" }));
    await screen.findByLabelText("系统 Prompt", { selector: "pre" });
    expect(api).toHaveBeenCalledTimes(2);
    expect(onRevalidate).not.toHaveBeenCalled();
  });

  it("keeps the explicit local revalidation action separate from viewing", async () => {
    show({ ...batch, calls: [{ ...calls[2], can_revalidate: true }] });
    open("memory:1");
    await screen.findByLabelText("系统 Prompt", { selector: "pre" });
    expect(onRevalidate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "纯本地重验" }));
    expect(onRevalidate).toHaveBeenCalledExactlyOnceWith("call-2");
  });
});
