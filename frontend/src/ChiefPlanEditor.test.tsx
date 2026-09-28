import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ChiefPlanFields, ChiefPlanFiles, planMarkdown } from "./ChiefPlanEditor";
import type { GenerationDetail } from "./api";

const plan = { chapter_goal: "主动邀请", bridge: "承接渡口", major_turn: "选择留下", genre_causal_role: "关系改变行动", opening_focus_percent: 0, questions: [], story_questions: ["谁先开口"], future_proposal: "后续同行", author_question_reasons: {}, scenes: [0, 1].map(() => ({ event: "相邀", character_ids: ["a", "b"], choice_and_response: "邀请并回应", consequence: "一同出发", focus_percent: 50, transition_percent: 0, other_percent: 0 })) };
afterEach(cleanup);
it("shows equal allocation only for bound new stages and preserves legacy weight editing", () => {
  const weighted = { ...plan, scenes: plan.scenes.map((s) => ({ ...s, size_weight: 3, development: { onstage_process: "一轮完整行动" } })) };
  const changed = vi.fn();
  const { rerender } = render(<ChiefPlanFields value={JSON.stringify(weighted)} onChange={changed} writtenUnits={0} disabled={false} balancedUnits />);
  expect(screen.getByText(/本阶段按有效单元数均分篇幅/)).toBeInTheDocument();
  expect(screen.queryByLabelText("展开份量权重")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("单元 1 · 后果"), { target: { value: "完成交易并改变调查条件" } });
  expect(JSON.parse(changed.mock.calls[0][0]).scenes[0].size_weight).toBe(3);
  rerender(<ChiefPlanFields value={JSON.stringify(weighted)} onChange={changed} writtenUnits={0} disabled={false} />);
  expect(screen.getAllByLabelText("展开份量权重")[0]).toHaveValue(3);
});
it("shows proposed characters as designs and preserves them when selecting scene cast", () => {
  const expanded = { ...plan, new_characters: [{ id: "new-person", name: "林岑", description: "渡口向导", independent_goal: "找回灯笼", voice: "短句", entry_reason: "闻声赶来" }], creative_notes: ["来历可在后续展开"] };
  const changed = vi.fn();
  render(<ChiefPlanFields value={JSON.stringify(expanded)} onChange={changed} writtenUnits={0} disabled={false} characters={[{ id: "a", name: "原角色" }]} />);
  expect(screen.getByRole("region", { name: "新人物候选" })).toHaveTextContent("正文写出并提取证据后才形成候选事实");
  expect(screen.getByText("创作待定事项（无需答复）")).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("单元 1 · 林岑"));
  const saved = JSON.parse(changed.mock.calls[0][0]);
  expect(saved.scenes[0].character_ids).toContain("new-person");
  expect(saved.new_characters).toEqual(expanded.new_characters);
  expect(planMarkdown(expanded)).toContain("找回灯笼");
  expect(planMarkdown(expanded)).toContain("来历可在后续展开");
});

it("edits and exports background plans without percentage controls", () => {
  const { genre_causal_role, opening_focus_percent, ...rest } = plan;
  const background = { ...rest, world_context: "夜间渡口关闭，来自正式规则", scenes: rest.scenes.map(({ focus_percent, transition_percent, other_percent, ...scene }) => scene) };
  const changed = vi.fn();
  render(<ChiefPlanFields value={JSON.stringify(background)} onChange={changed} writtenUnits={0} disabled={false} />);
  expect(screen.queryByLabelText("开篇过渡份额（%）")).not.toBeInTheDocument();
  expect(screen.queryByText(/主导份额/)).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("世界观与背景依据"), { target: { value: "正式设定保持" } });
  expect(JSON.parse(changed.mock.calls[0][0]).world_context).toBe("正式设定保持");
  const markdown = planMarkdown(background);
  expect(markdown).toContain("世界观与背景依据");
  expect(markdown).not.toContain("undefined");
  expect(markdown).not.toContain("%");
});
it("keeps malformed advanced JSON editable without crashing the workspace", () => {
  render(<ChiefPlanFields value='{"scenes":[null]}' onChange={vi.fn()} writtenUnits={0} disabled={false} />);
  expect(screen.getByRole("alert")).toHaveTextContent("JSON 格式无效");
  expect(screen.getByLabelText("有效故事方案（JSON）")).toHaveValue('{"scenes":[null]}');
});
it("edits human-readable direction while retaining the complete automated contract", () => {
  const changed = vi.fn();
  render(<ChiefPlanFields value={JSON.stringify(plan)} onChange={changed} writtenUnits={0} disabled={false} />);
  fireEvent.change(screen.getByLabelText("阶段目标"), { target: { value: "正式表达心意" } });
  expect(JSON.parse(changed.mock.calls[0][0])).toEqual({ ...plan, chapter_goal: "正式表达心意" });
  expect(screen.getByLabelText("有效故事方案（JSON）")).toBeTruthy();
  expect(planMarkdown(plan)).toContain("谁先开口");
});
it("locks the original goal and written units but permits the unwritten suffix", () => {
  render(<ChiefPlanFields value={JSON.stringify(plan)} onChange={vi.fn()} writtenUnits={1} disabled={false} />);
  expect(screen.getByLabelText("阶段目标")).toBeDisabled();
  expect(screen.getByLabelText("单元 1 · 事件")).toBeDisabled();
  expect(screen.getByLabelText("单元 2 · 事件")).not.toBeDisabled();
});
it("preserves selectable original versions and exports the selected saved plan", () => {
  const current = { ...plan, chapter_goal: "作者新方向" };
  const batch = { id: "batch", state: { plan_id: "current" }, artifacts: [{ id: "original", kind: "plan", sha256: "old-sha", payload: plan }, { id: "current", kind: "plan", sha256: "new-sha", payload: current }] } as unknown as GenerationDetail;
  const create = vi.fn().mockReturnValue("blob:test"); vi.stubGlobal("URL", { createObjectURL: create, revokeObjectURL: vi.fn() });
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  render(<ChiefPlanFiles batch={batch} />);
  fireEvent.change(screen.getByLabelText("查看方案版本"), { target: { value: "original" } });
  expect(screen.getByText(/# Chief 故事方案/)).toHaveTextContent("主动邀请");
  fireEvent.click(screen.getByText("下载完整方案 JSON"));
  expect(create).toHaveBeenCalledOnce(); expect(click).toHaveBeenCalledOnce();
  click.mockRestore(); vi.unstubAllGlobals();
});
