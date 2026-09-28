import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { StageSettings } from "./LongformStage";
import { countStoryCharacters, StageScaleProgress } from "./StageScale";
import { StageContinuation } from "./StageContinuation";
import { ChapterArrangement } from "./ChapterArrangement";
import type { GenerationDetail, GenerationSpec } from "./api";

const mocks = vi.hoisted(() => ({ api: vi.fn(), write: vi.fn() }));
vi.mock("./api", async (original) => ({ ...await original<typeof import("./api")>(), api: mocks.api, StableWriteOperationKeys: class { request = mocks.write; } }));
afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

it("counts the same non-whitespace Unicode code points as the backend", () => {
  expect(countStoryCharacters("汉，𠮷🙂\n\r\t\u3000\u0085\u001c")).toBe(4);
});

it("preserves the author's longform count and scale across mode changes", () => {
  function Form() {
    const [spec, setSpec] = useState({ stage_mode: "longform-v1", unit_limit: 6, stage_scale: { scale_mode: "stage-range", min_characters: 16000, max_characters: 21000, preferred_units: 6 } } as GenerationSpec);
    return <StageSettings spec={spec} update={(patch) => setSpec({ ...spec, ...patch })} history={[]} />;
  }
  render(<Form />);
  fireEvent.change(screen.getByLabelText("生成方式"), { target: { value: "single-unit-v1" } });
  expect(screen.queryByLabelText("阶段目标下界（字）")).toBeNull();
  fireEvent.change(screen.getByLabelText("生成方式"), { target: { value: "longform-v1" } });
  expect(screen.getByLabelText("阶段目标下界（字）")).toHaveValue(16000);
  expect(screen.getByLabelText("首选叙事单元数")).toHaveValue(6);
});

it("reports short completed prose without offering an automatic rewrite", () => {
  const batch = { state: { units_finished: true, stage_scale_status: { characters: 8000, status: "below", deficit: 7000, target: { min_characters: 15000, max_characters: 20000 } } } } as unknown as GenerationDetail;
  render(<StageScaleProgress batch={batch} />);
  expect(screen.getByLabelText("阶段篇幅")).toHaveTextContent("低于目标 7,000 字");
  expect(screen.queryByRole("button")).toBeNull();
});

it("invalidates confirmation when only the remaining-request receipt changes", () => {
  const batch = { id: "b", preview_sha256: "preview", status: "awaiting_plan", spec: { craft_policy: "stage-craft-v1" }, state: { plan_id: "plan", plan_adjustment_id: "adjustment" }, calls: [{ status: "completed" }], artifacts: [{ id: "plan", sha256: "plan-sha", payload: {} }, { id: "adjustment", sha256: "first-sha", payload: { input_count: 1000, remaining_unit_slots: 4, max_cost_cny: "5", blockers: [] } }] } as unknown as GenerationDetail;
  const { rerender } = render(<StageContinuation batch={batch} base="/b" disabled={false} onContinued={vi.fn()} />);
  fireEvent.click(screen.getByRole("checkbox"));
  expect(screen.getByRole("button")).toBeEnabled();
  rerender(<StageContinuation batch={{ ...batch, artifacts: [batch.artifacts[0], { ...batch.artifacts[1], sha256: "second-sha" }] }} base="/b" disabled={false} onContinued={vi.fn()} />);
  expect(screen.getByRole("button")).toBeDisabled();
  expect(mocks.write).not.toHaveBeenCalled();
});

it("previews paragraph IDs with Unicode offsets and discards changed boundary approval", async () => {
  const batch = { id: "b", state: { candidate_id: "c", segments_id: "s", units_finished: true }, artifacts: [{ id: "c", sha256: "body", payload: { body: "𠮷，第一段。\n\n第二段。" } }, { id: "s", sha256: "manifest", payload: { segments: [{ end: 12 }] } }], passages: [{ id: "p1", start: 0, end: 6 }, { id: "p2", start: 8, end: 12 }] } as unknown as GenerationDetail;
  mocks.api.mockResolvedValue({ preview_sha256: "bound", chapters_requiring_position: [1], notice: "重新核对", manifest: { segments: [{}, {}] } });
  render(<ChapterArrangement batch={batch} base="/b" disabled={false} refresh={vi.fn()} />);
  const boundary = screen.getByRole("checkbox");
  expect(screen.getByText(/在第 1 段后分章：𠮷，第一段。/)).toBeTruthy();
  fireEvent.click(boundary);
  fireEvent.click(screen.getByText("预览新章节"));
  await screen.findByText("确认新章界并重新核对逐章事实");
  expect(JSON.parse(mocks.api.mock.calls[0][1].body).paragraph_ends).toEqual(["p1"]);
  fireEvent.click(boundary);
  await waitFor(() => expect(screen.queryByText("确认新章界并重新核对逐章事实")).toBeNull());
  expect(mocks.write).not.toHaveBeenCalled();
});
