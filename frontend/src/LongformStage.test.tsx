import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { LongformStage, StageSettings } from "./LongformStage";
import type { GenerationDetail, GenerationSpec } from "./api";

const mocks = vi.hoisted(() => ({ api: vi.fn(), write: vi.fn() }));
vi.mock("./api", async (original) => ({ ...await original<typeof import("./api")>(), api: mocks.api, StableWriteOperationKeys: class { request = mocks.write; } }));
afterEach(cleanup);
const batch = { id: "b", status: "needs_attention", revision: "genre-led-longform-v1", spec: { stage_mode: "longform-v1", unit_limit: 2 }, state: { candidate_id: "c", segments_id: "s", units_id: "u" }, next_action: null, artifacts: [{ id: "c", payload: { body: "林青发出邀请。江月作出回应。" } }, { id: "u", payload: { items: [{ ordinal: 1, start: 0, end: 8, memory_id: "m" }, { ordinal: 2, start: 8, end: 16, memory_id: "n" }] } }] } as unknown as GenerationDetail;
const suggestions = { candidate_sha256: "candidate", manifest_sha256: "manifest", tail: null, deferred_fact_count: 0, chapters: [1, 2].map((n) => ({ id: `chapter-${n}`, number: n, ordinal: n, start: (n - 1) * 8, end: n * 8, position: { current_location: "渡口", recent_major_event: "自主回应" }, position_status: "known", factual_changes: {}, observations: [], diagnostics: [], facts_status: "complete" })) };
beforeEach(() => { vi.clearAllMocks(); mocks.api.mockImplementation(async (path: string) => path.endsWith("stage-chapters") ? suggestions : { preview_sha256: "bound-preview", chapters: [{}], full_stage: false }); });

it("separates unit count from chapters and freezes only an explicitly chosen prefix", async () => {
  render(<LongformStage batch={batch} base="/batches" disabled={false} onAdopted={vi.fn()} />);
  expect(await screen.findByText("第 1 章 · 8 字")).toBeTruthy();
  expect(screen.getByText(/已保存 2 \/ 2 个单元/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("本次采用范围"), { target: { value: "1" } });
  expect(screen.queryByText("第 2 章 · 8 字")).toBeNull();
  expect(screen.getByText("预览阶段采用")).toBeDisabled();
  fireEvent.click(screen.getByLabelText("已核对本章事实和现场，没有带入后章结果"));
  fireEvent.click(screen.getByText("预览阶段采用"));
  const adopt = await screen.findByText("确认采用阶段并创建正式版本");
  expect(adopt).toBeDisabled();
  const submitted = JSON.parse(mocks.api.mock.calls.find((call) => call[0].endsWith("stage-adoption-preview"))![1].body);
  expect(submitted.chapters).toHaveLength(1);
  expect(mocks.write).not.toHaveBeenCalled();
  fireEvent.click(screen.getByLabelText("已读稿并接受当前题材写法"));
  fireEvent.click(adopt);
  await waitFor(() => expect(mocks.write).toHaveBeenCalledTimes(1));
  expect(JSON.parse(mocks.write.mock.calls[0][1].body).preview_sha256).toBe("bound-preview");
});

it("unapplied facts disable confirmation and invalidate a previous preview", async () => {
  render(<LongformStage batch={batch} base="/batches" disabled={false} onAdopted={vi.fn()} />);
  await screen.findByLabelText("本次采用范围");
  fireEvent.change(screen.getByLabelText("本次采用范围"), { target: { value: "1" } });
  const check = screen.getByLabelText("已核对本章事实和现场，没有带入后章结果");
  fireEvent.click(check);
  fireEvent.change(screen.getByLabelText("本章事实变化"), { target: { value: "broken" } });
  expect(check).toBeDisabled();
  expect(screen.getByText("预览阶段采用")).toBeDisabled();
  fireEvent.click(screen.getByText("应用事实修正"));
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(mocks.write).not.toHaveBeenCalled();
});

it("enables longform explicitly without upgrading an old single-unit setting", () => {
  const update = vi.fn();
  render(<StageSettings spec={{ unit_limit: 1, chapter_count: 1 } as GenerationSpec} update={update} history={[]} />);
  expect(screen.getByLabelText("生成方式")).toHaveValue("single-unit-v1");
  fireEvent.change(screen.getByLabelText("生成方式"), { target: { value: "longform-v1" } });
  expect(update).toHaveBeenCalledWith(expect.objectContaining({ stage_mode: "longform-v1", unit_limit: 3, previous_stage_id: null }));
});

it("identifies missing chapter fields locally and validates only the selected prefix", async () => {
  const missing = { ...suggestions, chapters: suggestions.chapters.map((c) => ({ ...c, position: null, position_status: "author-required" })) };
  mocks.api.mockImplementation(async (path: string) => path.endsWith("stage-chapters") ? missing : { preview_sha256: "bound-preview", chapters: [{}] });
  render(<LongformStage batch={batch} base="/batches" disabled={false} onAdopted={vi.fn()} />);
  expect(await screen.findByText(/第 1 章还需补充：章末地点、本章实际事件/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("本次采用范围"), { target: { value: "1" } });
  fireEvent.click(screen.getByLabelText("已核对本章事实和现场，没有带入后章结果"));
  expect(screen.getByText("预览阶段采用")).toBeDisabled();
  expect(mocks.api).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByLabelText("章末地点"), { target: { value: "渡口" } });
  fireEvent.change(screen.getByLabelText("本章实际事件"), { target: { value: "交出船票" } });
  expect(screen.getByText("预览阶段采用")).toBeDisabled();
  fireEvent.click(screen.getByLabelText("已核对本章事实和现场，没有带入后章结果"));
  expect(screen.getByText("预览阶段采用")).not.toBeDisabled();
  fireEvent.change(screen.getByLabelText("章末地点"), { target: { value: "  " } });
  expect(screen.getByLabelText("章末地点")).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByText("预览阶段采用")).toBeDisabled();
});

it("shows earlier scene references as editable advice alongside the remaining prose", async () => {
  mocks.api.mockResolvedValue({ ...suggestions, chapters: [{ ...suggestions.chapters[0], position_status: "reference", position_source: { end: 4, remaining_characters: 4 } }] });
  render(<LongformStage batch={batch} base="/batches" disabled={false} onAdopted={vi.fn()} />);
  expect(await screen.findByText(/已带入本章内较早的接力参考/)).toBeTruthy();
  expect(screen.getByText("参考之后至章末还有 4 字，请核对现场变化")).toBeTruthy();
  expect(screen.getByLabelText("章末地点")).toHaveValue("渡口");
  expect(screen.getByLabelText("本章实际事件")).toHaveValue("自主回应");
  expect(screen.getByLabelText("已核对本章事实和现场，没有带入后章结果")).not.toBeChecked();
  expect(mocks.write).not.toHaveBeenCalled();
});

it("ignores a preview that arrives after the author changes the chapter scene", async () => {
  let resolve!: (value: unknown) => void;
  mocks.api.mockImplementation(async (path: string) => path.endsWith("stage-chapters") ? suggestions : new Promise((done) => { resolve = done; }));
  render(<LongformStage batch={batch} base="/batches" disabled={false} onAdopted={vi.fn()} />);
  await screen.findByLabelText("本次采用范围");
  fireEvent.change(screen.getByLabelText("本次采用范围"), { target: { value: "1" } });
  fireEvent.click(screen.getByLabelText("已核对本章事实和现场，没有带入后章结果"));
  fireEvent.click(screen.getByText("预览阶段采用"));
  fireEvent.change(screen.getByLabelText("章末地点"), { target: { value: "码头外" } });
  fireEvent.click(screen.getByLabelText("已核对本章事实和现场，没有带入后章结果"));
  resolve({ preview_sha256: "stale", chapters: [{}], full_stage: false });
  await waitFor(() => expect(screen.getByText("预览阶段采用")).not.toBeDisabled());
  expect(screen.queryByText("确认采用阶段并创建正式版本")).toBeNull();
});
