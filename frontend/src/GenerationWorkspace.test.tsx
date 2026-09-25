import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GenerationWorkspace } from "./GenerationWorkspace";
import { deleteLocalDraft, readLocalDraft, writeLocalDraft } from "./localDrafts";

const mocks = vi.hoisted(() => ({ api: vi.fn(), write: vi.fn() }));
vi.mock("./api", async (original) => ({ ...await original<typeof import("./api")>(), api: mocks.api, StableWriteOperationKeys: class { request = mocks.write; } }));
vi.mock("./localDrafts", () => ({ readLocalDraft: vi.fn().mockResolvedValue(null), writeLocalDraft: vi.fn().mockResolvedValue(undefined), deleteLocalDraft: vi.fn().mockResolvedValue(undefined), sha256: vi.fn().mockResolvedValue("body-hash") }));
const spec = { base_version_id: "base-version", focus_card_id: "girls_love_gl", direction: "爱情影响行动", character_ids: ["a", "b"], viewpoint: "林青", relationship_scope: "explore", relationship_character_ids: ["a", "b"], profile_id: "fixture", chief_model: "m", writer_model: "m", max_cost_cny: "10" };
const setup = { configuration_revision: "author-intent-v1", context_budget_revision: "chief-focus-v4", output_budget_revision: "chief-output-v1", automation_revision: "stage-auto-v1", base_version_id: "base-version", version: 1, characters: [{ id: "a", name: "林青" }, { id: "b", name: "江月" }], narrative_position: {}, style: { selection_mode: "specified", genre_card_id: "girls_love_gl", secondary_genre_card_ids: [], matched_cards: [{ id: "girls_love_gl", name: "百合" }] } };
const saved = { id: "batch", project_id: "p", status: "needs_attention", spec, revision: "genre-led-single-chapter-v1", preview_sha256: "preview", snapshot: { maximum_cost_cny: "1.20", plan_input_tokens: 45000, blockers: [] }, state: { candidate_id: "candidate", message: "复核不可用，正文已保存" }, next_action: null, artifacts: [{ id: "candidate", kind: "candidate", sha256: "candidate-sha", payload: { body: "林青希望她留下。", complete: true } }], calls: [] };

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  mocks.api.mockReset();
  mocks.write.mockReset();
  vi.mocked(readLocalDraft).mockResolvedValue(null);
  mocks.api.mockImplementation(async (path: string) => {
    if (path.endsWith("/setup")) return setup;
    if (path === "/api/provider-profiles") return [{ id: "fixture", display_name: "测试模型", enabled: true, allow_story_data: true, credential_required: false, default_model: "m", models: [{ id: "m" }] }];
    if (path.endsWith("/generation-batches")) return [{ id: "batch", direction: spec.direction, status: "needs_attention" }];
    if (path.includes("/input-preview")) return { preview_sha256: "input-preview", output_limit: 100000, all_roles: true, previous_input_limit: 58000, input_limit: 200000, writer_input_tokens: 63120, spent_cost_cny: "0.48", remaining_cost_upper_cny: "7.098", total_cost_upper_cny: "7.578", max_cost_cny: "10", blockers: [] };
    if (path.endsWith("/adoption-preview")) return { preview_sha256: "adopt-preview", candidate_sha256: "candidate-sha", needs_genre_acknowledgement: true, factual_delta: {} };
    return saved;
  });
  mocks.write.mockResolvedValue(saved);
});

describe("genre generation workspace", () => {
  it.each(["new", "blocked", "failed"])("uses manual selections for a %s preview without changing the frozen batch", async (entry) => {
    const cards = [{ id: "world", name: "奇幻", layer: "genre" }, ...["百合", "种田文", "推理"].map((name, i) => ({ id: `n${i}`, name, layer: "narrative" }))];
    const frozen = { ...saved, spec: { ...spec, card_selection_policy: "separate-v1", narrative_card_ids: ["old"] }, snapshot: { ...saved.snapshot, cards: [{ id: "old", name: "旧卡" }] } };
    const current = entry === "blocked" ? { ...frozen, status: "draft", next_action: "plan", state: {}, artifacts: [], snapshot: { ...frozen.snapshot, blockers: ["输入过大"] } }
      : entry === "failed" ? { ...frozen, state: {}, artifacts: [], plan_retry_preview: { reason: "obsolete_genre_quota", chief_output_limit: 100000 } } : frozen;
    const original = mocks.api.getMockImplementation()!;
    mocks.api.mockImplementation(async (path: string) => path.endsWith("/setup") ? { ...setup, available_cards: cards, style: { ...setup.style, genre_card_id: "world" } } : path.endsWith("/batch") ? current : original(path));
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText("叙事卡选择方式"), { target: { value: "manual" } });
    for (const name of ["百合", "种田文", "推理"]) fireEvent.click(screen.getByLabelText(`叙事卡：${name}`));
    expect(screen.getByLabelText("本阶段叙事卡")).toHaveTextContent("旧卡");
    const button = entry === "blocked" ? "重新检查并建立预览（不调用模型）" : entry === "failed" ? "取消旧题材配额并建立新预览（不调用模型）" : "建立新预览（不调用模型）";
    fireEvent.click(screen.getByText(button));
    await waitFor(() => expect(mocks.write).toHaveBeenCalledOnce());
    expect(mocks.write.mock.calls[0][0]).toBe("/api/projects/p/generation-batches");
    const body = JSON.parse(mocks.write.mock.calls[0][1].body);
    expect(body).toMatchObject({ focus_card_id: "world", narrative_card_ids: ["n0", "n1", "n2"] });
    expect(body).not.toHaveProperty("narrative_selection_mode");
    expect(current.spec.narrative_card_ids).toEqual(["old"]);
  });

  it("can switch back to random without sending the remembered manual choices", async () => {
    const original = mocks.api.getMockImplementation()!;
    const cards = [{ id: "world", name: "奇幻", layer: "genre" }, { id: "story", name: "种田文", layer: "narrative" }];
    mocks.api.mockImplementation(async (path: string) => path.endsWith("/setup") ? { ...setup, available_cards: cards, style: { ...setup.style, genre_card_id: "world" } } : original(path));
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText("叙事卡选择方式"), { target: { value: "manual" } });
    fireEvent.click(screen.getByLabelText("叙事卡：种田文"));
    fireEvent.change(screen.getByLabelText("叙事卡选择方式"), { target: { value: "random" } });
    fireEvent.click(screen.getByText("建立新预览（不调用模型）"));
    await waitFor(() => expect(mocks.write).toHaveBeenCalledOnce());
    expect(mocks.write.mock.calls[0][0]).toBe("/api/projects/p/generation-batches/random-preview");
    expect(JSON.parse(mocks.write.mock.calls[0][1].body).narrative_card_ids).toEqual([]);
    fireEvent.change(screen.getByLabelText("叙事卡选择方式"), { target: { value: "manual" } });
    expect(screen.getByLabelText("叙事卡：种田文")).toBeChecked();
  });

  it("shows the confirmed save before browser draft cleanup finishes", async () => {
    let cleanupDraft!: () => void;
    vi.mocked(deleteLocalDraft).mockImplementationOnce(() => new Promise<void>((resolve) => { cleanupDraft = resolve; }));
    const next = { ...saved, state: { candidate_id: "new-candidate" }, artifacts: [{ id: "new-candidate", kind: "candidate", sha256: "new-sha", payload: { body: "新的作者正文。", complete: true } }] };
    mocks.write.mockResolvedValueOnce(next);
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    fireEvent.change(await screen.findByDisplayValue("林青希望她留下。"), { target: { value: "新的作者正文。" } });
    fireEvent.click(screen.getByText("保存候选修改"));
    expect(await screen.findByText("已保存为新候选，旧复核不再用于此稿。")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "正文" })).toHaveValue("新的作者正文。");
    cleanupDraft();
    await waitFor(() => expect(screen.getByText("刷新状态")).toBeEnabled());
    expect(mocks.write).toHaveBeenCalledOnce();
  });

  it("acknowledges authorization immediately while the next detail request is pending", async () => {
    const draft = { ...saved, status: "draft", next_action: "plan", state: {}, artifacts: [] };
    const original = mocks.api.getMockImplementation()!;
    mocks.api.mockImplementation(async (path: string, ...args: unknown[]) => path.endsWith("/batch") ? draft : original(path, ...args));
    // Finish initial effects before confirming; changing batches clears consent.
    await act(async () => { render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />); });
    fireEvent.click(await screen.findByLabelText("确认本批模型、完整题材卡及选中故事资料的外发范围与费用上限"));
    let finishRead!: () => void;
    mocks.api.mockImplementation(async (path: string, ...args: unknown[]) => path.endsWith("/batch") ? new Promise((resolve) => { finishRead = () => resolve({ ...draft, status: "running" }); }) : original(path, ...args));
    mocks.write.mockResolvedValueOnce({ id: "batch", status: "queued" });
    fireEvent.click(screen.getByText("授权并开始一章创作"));
    expect(await screen.findByText("本次响应保存后暂停")).toBeEnabled();
    expect(screen.queryByText("授权并开始一章创作")).toBeNull();
    expect(mocks.write).toHaveBeenCalledOnce();
    await act(async () => { finishRead(); });
  });

  it("opens role prompts from the stage status without starting a model call", async () => {
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "查看各角色 Prompt 与输出" }));
    expect(screen.getByRole("tab", { name: "调用详情" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("heading", { name: "各角色 Prompt 与输出" })).toBeVisible();
    expect(mocks.write).not.toHaveBeenCalled();
  });
  it("requests a random narrative pair and displays the frozen names before authorization", async () => {
    const cards = [
      { id: "world", name: "奇幻", layer: "genre" }, { id: "side", name: "仙侠", layer: "genre" },
      ...["百合", "种田文", "推理"].map((name, index) => ({ id: `n-${index}`, name, layer: "narrative" })),
    ];
    const original = mocks.api.getMockImplementation()!;
    mocks.api.mockImplementation(async (path: string) => path.endsWith("/setup") ? { ...setup, available_cards: cards, style: { ...setup.style, genre_card_id: "world", matched_cards: [cards[0]] } } : original(path));
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText("副题材（可选）"), { target: { value: "side" } });
    expect(screen.getByText("叙事卡：每次随机两张")).toBeInTheDocument();
    expect(screen.queryByLabelText("叙事卡：百合")).toBeNull();
    mocks.write.mockResolvedValue({ ...saved, status: "draft", next_action: "plan", state: {}, artifacts: [],
      spec: { ...saved.spec, card_selection_policy: "separate-v1", narrative_card_ids: ["n-2", "n-0"] },
      snapshot: { ...saved.snapshot, narrative_selection_policy: "random-two-v1", cards: [{ id: "n-2", name: "冻结推理" }, { id: "n-0", name: "冻结百合" }] },
    });
    fireEvent.click(screen.getByText("建立新预览（不调用模型）"));
    await waitFor(() => expect(mocks.write).toHaveBeenCalledOnce());
    expect(JSON.parse(mocks.write.mock.calls[0][1].body)).toMatchObject({ focus_card_id: "world", supporting_card_id: "side", narrative_card_ids: [], card_selection_policy: "separate-v1" });
    expect(mocks.write.mock.calls[0][0]).toBe("/api/projects/p/generation-batches/random-preview");
    expect(await screen.findByText("本次随机叙事卡")).toBeVisible();
    expect(screen.getByLabelText("本阶段叙事卡")).toHaveTextContent("冻结推理、冻结百合");
    expect(screen.getByText("授权并开始一章创作")).toBeDisabled();
    fireEvent.click(screen.getByText("刷新状态"));
    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith("/api/projects/p/generation-batches/batch"));
    expect(mocks.write).toHaveBeenCalledOnce();
  });
  it("offers a new background preview for a saved quota failure without authorizing calls", async () => {
    const failed = { ...saved, revision: "genre-led-longform-v1", state: {}, artifacts: [], plan_retry_preview: { reason: "obsolete_genre_quota", chief_output_limit: 100000 }, calls: [{ id: "chief", action: "plan", status: "local_failure", diagnostic: { code: "obsolete_genre_quota", message: "六个单元份额合计600%，旧配额拒绝接纳方案。" } }] };
    const original = mocks.api.getMockImplementation()!;
    mocks.api.mockImplementation(async (path: string) => path.endsWith("/generation-batches/batch") ? failed : original(path));
    mocks.write.mockResolvedValue({ ...saved, status: "draft", artifacts: [], state: {}, calls: [] });
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    const button = await screen.findByText("取消旧题材配额并建立新预览（不调用模型）");
    expect(mocks.write).not.toHaveBeenCalled();
    fireEvent.click(button);
    await waitFor(() => expect(mocks.write).toHaveBeenCalledTimes(1));
    expect(mocks.write.mock.calls[0][0]).toBe("/api/projects/p/generation-batches/random-preview");
    expect(JSON.parse(mocks.write.mock.calls[0][1].body)).toMatchObject({ writing_policy: "guided-v1", narrative_policy: "plot-led-v3", feedback_policy: "logic-v1" });
  });
  it("keeps logic review and removes Reader and milestone options from new previews", async () => {
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    await screen.findByLabelText("完成正文后核对逻辑矛盾");
    expect(screen.queryByLabelText("完成正文后听取 Reader 阅读感受")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("中途复核剧情计划（可选）")).not.toBeInTheDocument();
    expect(screen.getByLabelText("生成方式")).toBeInTheDocument();
    expect(screen.getByLabelText("预留独立标题动作")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("完成正文后核对逻辑矛盾"));
    await waitFor(() => expect(screen.getByText("建立新预览（不调用模型）")).toBeEnabled());
    fireEvent.click(screen.getByText("建立新预览（不调用模型）"));
    await waitFor(() => expect(mocks.write).toHaveBeenCalled());
    expect(JSON.parse(mocks.write.mock.calls[0][1].body)).toMatchObject({ feedback_policy: "logic-v1", enable_reader: false, enable_checker: false });
  });
  it("keeps independent model and tokenizer selections at 100000 and exposes role output limits", async () => {
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    await screen.findByLabelText("本次输入上限");
    for (const role of ["memory", "checker", "editor"]) {
      expect(screen.getByLabelText(`${role} 输出上限`)).toHaveValue(100000);
      fireEvent.change(screen.getByLabelText(`${role} 模型`), { target: { value: "m" } });
      fireEvent.change(screen.getByLabelText(`${role} 分词配置`), { target: { value: "test-count" } });
      expect(screen.getByLabelText(`${role} 输出上限`)).toHaveValue(100000);
    }
    await waitFor(() => expect(screen.getByText("建立新预览（不调用模型）")).toBeEnabled());
    fireEvent.click(screen.getByText("建立新预览（不调用模型）"));
    await waitFor(() => expect(mocks.write).toHaveBeenCalledOnce());
    const submitted = JSON.parse(mocks.write.mock.calls[0][1].body);
    expect(submitted).toMatchObject({ input_limit: 200000, chief_output_limit: 100000, writer_output_limit: 100000, auxiliary_output_limit: 100000 });
    for (const role of ["memory", "checker", "editor"]) expect(submitted.roles[role]).toMatchObject({ output_limit: 100000, tokenizer_id: "test-count" });
  });
  it("migrates an old browser input default once and retains later manual edits on reload", async () => {
    const draft = { schema_version: "local-draft-v1" as const, draft_key: "generation-form:p", project_id: "p", surface: "generation", resource_id: "p", base_version: 1, base_sha256: null, payload: { ...spec, input_limit: 58000 }, payload_sha256: "old", updated_at: "2026-09-22" };
    vi.mocked(readLocalDraft).mockImplementation(async (key) => key === draft.draft_key ? draft as never : null);
    const view = render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    expect(await screen.findByLabelText("本次输入上限")).toHaveValue(200000);
    fireEvent.change(screen.getByLabelText("本次输入上限"), { target: { value: "72000" } });
    await waitFor(() => expect(writeLocalDraft).toHaveBeenCalledWith(expect.objectContaining({ payload: expect.objectContaining({ input_limit: 72000, input_defaults_revision: "input-200k-v2" }) })));
    const stored = vi.mocked(writeLocalDraft).mock.calls.at(-1)![0];
    view.unmount();
    vi.mocked(readLocalDraft).mockImplementation(async (key) => key === draft.draft_key ? stored as never : null);
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    expect(await screen.findByLabelText("本次输入上限")).toHaveValue(72000);
    await waitFor(() => expect(screen.getByText("建立新预览（不调用模型）")).toBeEnabled());
    fireEvent.click(screen.getByText("建立新预览（不调用模型）"));
    await waitFor(() => expect(mocks.write).toHaveBeenCalledOnce());
    const sent = JSON.parse(mocks.write.mock.calls[0][1].body);
    expect(sent.input_limit).toBe(72000);
    expect(sent).not.toHaveProperty("input_defaults_revision");
  });
  it("saves edited Chief direction as a version without starting a model and preserves drafts across tabs", async () => {
    const previous = mocks.api.getMockImplementation()!;
    const plan = { chapter_goal: "旧目标", bridge: "前文", major_turn: "转折", genre_causal_role: "关系改变行动", opening_focus_percent: 0, questions: [], story_questions: ["悬念"], author_question_reasons: {}, scenes: [{ event: "相邀", character_ids: ["a", "b"], focus_percent: 100, transition_percent: 0, other_percent: 0, choice_and_response: "主动回应", consequence: "同行" }] };
    const batch = { ...saved, revision: "genre-led-longform-v1", plan_edit_revision: "author-plan-v1", input_recovery_available: true, state: { plan_id: "plan", message: "最终输入计数/上界 63120 超过允许值 58000" }, calls: [{ id: "chief", action: "plan", status: "completed" }], artifacts: [{ id: "plan", kind: "plan", sha256: "plan-sha", payload: plan }] };
    mocks.api.mockImplementation(async (path: string, ...args: unknown[]) => path.endsWith("/batch") ? batch : previous(path, ...args));
    mocks.write.mockImplementation(async (_: string, init: { body: string }) => ({ ...batch, state: { plan_id: "edited" }, artifacts: [...batch.artifacts, { id: "edited", kind: "plan", sha256: "edited-sha", payload: JSON.parse(init.body).plan }] }));
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    fireEvent.click(await screen.findByText("编辑 Chief 方案与故事方向"));
    fireEvent.change(await screen.findByLabelText("阶段目标"), { target: { value: "让主动告白改变共同出行" } });
    fireEvent.change(screen.getByLabelText("答复与修改说明"), { target: { value: "加强关系对选择的影响" } });
    expect(screen.getByText("核算剩余输入与费用（不调用模型）")).toBeDisabled();
    fireEvent.click(screen.getByRole("tab", { name: "调用详情" }));
    fireEvent.click(screen.getByRole("tab", { name: /故事方案/ }));
    expect(screen.getByLabelText("阶段目标")).toHaveValue("让主动告白改变共同出行");
    let finishCleanup!: () => void;
    vi.mocked(deleteLocalDraft).mockImplementationOnce(() => new Promise<void>((resolve) => { finishCleanup = resolve; }));
    vi.mocked(readLocalDraft).mockClear();
    vi.mocked(readLocalDraft).mockResolvedValue({ payload: { planText: JSON.stringify(plan), authorNote: "加强关系对选择的影响", questionAnswers: {}, deferredQuestions: [], baseSha: "plan-sha" } } as never);
    fireEvent.click(screen.getByText("保存方案（不调用模型）"));
    await screen.findByText("已保存新的故事方案版本，Chief 原稿保留；尚未调用模型。");
    expect(screen.queryByText("恢复方案草稿")).toBeNull();
    expect(screen.getByLabelText("答复与修改说明")).toHaveValue("");
    expect(readLocalDraft).not.toHaveBeenCalled();
    finishCleanup();
    await waitFor(() => expect(screen.getByText("刷新状态")).toBeEnabled());
    expect(mocks.write).toHaveBeenCalledTimes(1);
    expect(mocks.write.mock.calls[0][0]).toBe("/api/projects/p/generation-batches/batch/plan");
    expect(JSON.parse(mocks.write.mock.calls[0][1].body)).toMatchObject({ expected_plan_sha256: "plan-sha", plan: { chapter_goal: "让主动告白改变共同出行", story_questions: ["悬念"], author_question_reasons: {} } });
  });
  it("submits all token limits at 100000 for a preview without authorizing it", async () => {
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    await screen.findByLabelText("本次输入上限");
    for (const label of ["本次输入上限", "Chief 输出上限", "Writer 输出上限", "其他角色默认输出上限"]) {
      const input = screen.getByLabelText(label);
      expect(input).toHaveAttribute("max", label === "本次输入上限" ? "200000" : "100000");
      fireEvent.change(input, { target: { value: label === "本次输入上限" ? "200000" : "100000" } });
    }
    await waitFor(() => expect(screen.getByText("建立新预览（不调用模型）")).toBeEnabled());
    fireEvent.click(screen.getByText("建立新预览（不调用模型）"));
    await waitFor(() => expect(mocks.write).toHaveBeenCalledTimes(1));
    expect(mocks.write.mock.calls[0][0]).toBe("/api/projects/p/generation-batches/random-preview");
    expect(JSON.parse(mocks.write.mock.calls[0][1].body)).toMatchObject({ input_limit: 200000, chief_output_limit: 100000, auxiliary_output_limit: 100000, writer_output_limit: 100000 });
  });
  it("guides reading into review without writing, and keeps edits when switching tabs", async () => {
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    const body = await screen.findByRole("textbox", { name: "正文" });
    await waitFor(() => expect(body).toHaveValue("林青希望她留下。"));
    expect(screen.getByRole("tab", { name: "候选正文" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("button", { name: "预览正式采用" })).toBeNull();
    expect(screen.getByText(/终稿反馈尚未完成。先读稿/)).toBeVisible();
    fireEvent.change(body, { target: { value: "仍未保存的作者修改。" } });
    fireEvent.click(screen.getByRole("button", { name: "下一步：查看反馈与采用" }));
    expect(await screen.findByRole("button", { name: "预览正式采用" })).toBeDisabled();
    expect(screen.queryByRole("textbox", { name: "正文" })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "候选正文 · 未保存" }));
    expect(screen.getByRole("textbox", { name: "正文" })).toHaveValue("仍未保存的作者修改。");
    expect(mocks.write).not.toHaveBeenCalled();
  });

  it("supports keyboard navigation between the content panels", async () => {
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    const tab = await screen.findByRole("tab", { name: "候选正文" });
    tab.focus();
    fireEvent.keyDown(tab, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "审核与采用" })).toHaveFocus();
    expect(screen.getByRole("tabpanel")).toHaveAccessibleName("审核与采用");
    fireEvent.keyDown(screen.getByRole("tab", { name: "审核与采用" }), { key: "Home" });
    expect(screen.getByRole("tabpanel")).toHaveAccessibleName("故事方案");
    expect(mocks.write).not.toHaveBeenCalled();
  });
  it("offers a larger Chief preview but never retries or confirms the fee automatically", async () => {
    const previous = mocks.api.getMockImplementation()!;
    const failed = { ...saved, spec: { ...spec, workflow: "novel-run-v1", chief_output_limit: 6000, input_limit: 58000 }, state: {}, artifacts: [], plan_retry_preview: { chief_output_limit: 24000, auxiliary_output_limit: 6000, previous_output_limit: 6000 }, calls: [{ id: "failed-call", action: "plan", status: "local_failure", diagnostic: { code: "output_limit_exceeded", visible_characters: 0, message: "可见结果为空，本地重验无法补出内容" } }] };
    mocks.api.mockImplementation(async (path: string, ...args: unknown[]) => path.endsWith("/batch") ? failed : previous(path, ...args));
    mocks.write.mockResolvedValue({ ...failed, id: "replacement", status: "draft", next_action: "plan", calls: [], plan_retry_preview: null });
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    expect(await screen.findByRole("heading", { name: "已暂停：Chief 设计剧情" })).toBeTruthy();
    expect(screen.queryByText("纯本地重验")).toBeNull();
    fireEvent.click(screen.getByText("提高 Chief 输出并重新预览（不调用模型）"));
    await waitFor(() => expect(mocks.write).toHaveBeenCalledTimes(1));
    expect(mocks.write.mock.calls[0][0]).toBe("/api/projects/p/generation-batches/random-preview");
    expect(JSON.parse(mocks.write.mock.calls[0][1].body)).toEqual({ ...failed.spec, input_limit: 200000, context_policy: "chief-focus-v4", chief_output_limit: 100000, auxiliary_output_limit: 100000, writer_output_limit: 100000, roles: {}, writing_policy: "guided-v1", narrative_policy: "plot-led-v3", plan_policy: "bounded-v1", length_policy: "unit-v1", chapter_count: null, target_characters: null, card_selection_policy: "separate-v1", supporting_card_id: null, narrative_card_ids: [], feedback_policy: "logic-v1", enable_checker: true, enable_reader: false, enable_editor: false, milestone_unit: null });
    expect(await screen.findByText("已按当前创作设置建立新预览；请核对费用后授权，尚未调用模型。")).toBeTruthy();
    expect(screen.getByText("授权并开始一章创作")).toBeDisabled();
  });

  it("rechecks a blocked preview at the new input default without authorizing any call", async () => {
    const previous = mocks.api.getMockImplementation()!;
    const blocked = { ...saved, status: "draft", next_action: "plan", state: {}, artifacts: [], snapshot: { ...saved.snapshot, blockers: ["输入超过 58000"] } };
    mocks.api.mockImplementation(async (path: string, ...args: unknown[]) => path.endsWith("/batch") ? blocked : previous(path, ...args));
    mocks.write.mockResolvedValue({ ...blocked, id: "replacement", snapshot: { ...blocked.snapshot, blockers: [] } });
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    await screen.findByText("输入超过 58000");
    fireEvent.click(screen.getByLabelText("确认本批模型、完整题材卡及选中故事资料的外发范围与费用上限"));
    expect(screen.getByText("授权并开始一章创作")).toBeDisabled();
    fireEvent.click(screen.getByText("重新检查并建立预览（不调用模型）"));
    await waitFor(() => expect(mocks.write).toHaveBeenCalledTimes(1));
    expect(mocks.write.mock.calls[0][0]).toBe("/api/projects/p/generation-batches/random-preview");
    expect(JSON.parse(mocks.write.mock.calls[0][1].body)).toEqual({ ...spec, input_limit: 200000, chief_output_limit: 100000, writer_output_limit: 100000, auxiliary_output_limit: 100000, roles: {}, context_policy: "chief-focus-v4", writing_policy: "guided-v1", narrative_policy: "plot-led-v3", plan_policy: "bounded-v1", length_policy: "unit-v1", chapter_count: null, target_characters: null, card_selection_policy: "separate-v1", supporting_card_id: null, narrative_card_ids: [], feedback_policy: "logic-v1", enable_checker: true, enable_reader: false, enable_editor: false, milestone_unit: null });
    expect(await screen.findByText("已建立新的检查预览，原记录保留；尚未授权或调用模型。")).toBeTruthy();
    expect(screen.getByText("授权并开始一章创作")).toBeDisabled();
  });

  it("does not request an empty batch or lose the current preview for the placeholder", async () => {
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    await screen.findByDisplayValue("林青希望她留下。");
    mocks.api.mockClear();
    fireEvent.change(screen.getByLabelText("创作记录"), { target: { value: "" } });
    await waitFor(() => expect(screen.getByLabelText("创作记录")).not.toBeDisabled());
    expect(mocks.api).not.toHaveBeenCalled();
    expect(screen.getByLabelText("创作记录")).toHaveValue("batch");
    expect(screen.getByRole("option", { name: "选择批次" })).toBeDisabled();
    expect(screen.getByDisplayValue("林青希望她留下。")).toBeTruthy();
    expect(mocks.write).not.toHaveBeenCalled();
  });

  it("holds new previews until the matching backend is loaded", async () => {
    const previous = mocks.api.getMockImplementation()!;
    mocks.api.mockImplementation(async (path: string, ...args: unknown[]) => path.endsWith("/setup") ? { ...setup, configuration_revision: undefined } : previous(path, ...args));
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    expect(await screen.findByText("简化创作设置需要加载新版后端；已有批次仍可查看和审核。")).toBeTruthy();
    expect(screen.getByText("建立新预览（不调用模型）")).toBeDisabled();
    expect(await screen.findByDisplayValue("林青希望她留下。")).toBeTruthy();
    expect(mocks.write).not.toHaveBeenCalled();
  });

  it("sets a unit limit without assigning a word count", async () => {
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText("叙事单元上限"), { target: { value: "5" } });
    fireEvent.click(screen.getByText("建立新预览（不调用模型）"));
    await waitFor(() => expect(mocks.write).toHaveBeenCalledTimes(1));
    expect(JSON.parse(mocks.write.mock.calls[0][1].body)).toMatchObject({ stage_mode: "longform-v1", length_policy: "unit-v1", chapter_count: null, target_characters: null, unit_limit: 5, milestone_unit: null });
  });

  it("new previews default to automatic cast and uninterrupted finite execution", async () => {
    const previous = mocks.api.getMockImplementation()!;
    mocks.api.mockImplementation(async (path: string, ...args: unknown[]) => path.endsWith("/generation-batches") ? [] : previous(path, ...args));
    mocks.write.mockResolvedValue({ ...saved, status: "draft", next_action: "plan", state: {}, artifacts: [], snapshot: { ...saved.snapshot, cast_selection: { candidates: [{ id: "a", name: "林青", reasons: ["当前现场"] }, { id: "b", name: "江月", reasons: ["剧情文字相关"] }], required_ids: [], omitted_ids: [] } } });
    render(<GenerationWorkspace projectId="new" onAdopted={vi.fn()} />);
    expect(await screen.findByLabelText("人物选择方式")).toHaveValue("chief-auto-v1");
    expect(screen.getByLabelText("Chief 完成方案后先暂停，供我审阅")).not.toBeChecked();
    fireEvent.click(screen.getByText("建立新预览（不调用模型）"));
    await waitFor(() => expect(mocks.write).toHaveBeenCalledTimes(1));
    const submitted = JSON.parse(mocks.write.mock.calls[0][1].body);
    expect(submitted.character_ids).toEqual([]);
    expect(submitted).toMatchObject({ relationship_scope: "genre-led", viewpoint: "", chief_model: "m", writer_model: "m" });
    expect(submitted.direction).toContain("围绕选中的叙事内容");
    expect(submitted.character_selection).toBe("chief-auto-v1");
    expect(submitted.pause_after_plan).toBe(false);
    expect(await screen.findByText("自动选角候选已冻结，Chief 将在本次设计调用中选择。")).toBeTruthy();
    expect(screen.getByText("授权并开始一章创作")).toBeDisabled();
  });

  it("keeps historical batches unchanged while new previews use automatic intent", async () => {
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    expect(await screen.findByLabelText("人物选择方式")).toHaveValue("chief-auto-v1");
    expect(screen.queryByLabelText("关系方向")).toBeNull();
    fireEvent.change(screen.getByLabelText("人物选择方式"), { target: { value: "chief-auto-v1" } });
    expect(screen.getByText("固定出场人物（可不选）")).toBeTruthy();
    fireEvent.click(screen.getByText("建立新预览（不调用模型）"));
    await waitFor(() => expect(mocks.write).toHaveBeenCalledTimes(1));
    expect(JSON.parse(mocks.write.mock.calls[0][1].body)).toMatchObject({ character_ids: [], relationship_scope: "genre-led", relationship_character_ids: [] });
    expect(saved.spec.relationship_scope).toBe("explore");
  });

  it("makes failed-review prose readable and requires explicit deviation acceptance before adoption", async () => {
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    expect(await screen.findByDisplayValue("林青希望她留下。")).toBeTruthy();
    expect(screen.getByText("复核不可用，正文已保存")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("当前地点"), { target: { value: "渡口" } });
    fireEvent.change(screen.getByLabelText("本章实际完成的事件"), { target: { value: "邀请同行" } });
    fireEvent.click(screen.getByText("预览正式采用"));
    const adopt = await screen.findByText("确认采用并创建正式版本");
    expect(adopt).toBeDisabled();
    expect(mocks.write).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText("我已读稿，接受题材偏离或复核未知，保留当前写法"));
    expect(adopt).not.toBeDisabled();
  });

  it("creating a preview never starts a paid call", async () => {
    mocks.write.mockResolvedValue({ ...saved, status: "draft", next_action: "plan", state: {}, artifacts: [] });
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    await screen.findByDisplayValue("林青希望她留下。");
    fireEvent.click(screen.getByText("建立新预览（不调用模型）"));
    await waitFor(() => expect(mocks.write).toHaveBeenCalledTimes(1));
    expect(mocks.write.mock.calls[0][0]).toBe("/api/projects/p/generation-batches/random-preview");
    expect(await screen.findByText("授权并开始一章创作")).toBeDisabled();
  });

  it("unsaved prose prevents adoption and batch switching", async () => {
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    const editor = await screen.findByDisplayValue("林青希望她留下。");
    fireEvent.change(editor, { target: { value: "她改变了决定。" } });
    expect(screen.getByText("预览正式采用")).toBeDisabled();
    expect(screen.getByLabelText("创作记录")).toBeDisabled();
    expect(screen.getByText("保存候选修改")).not.toBeDisabled();
  });

  it("loads Memory proposals for normal adoption and requires fact confirmation", async () => {
    const novel = { ...saved, revision: "genre-led-novel-run-v1", spec: { ...spec, workflow: "novel-run-v1" }, state: { ...saved.state, memory_id: "memory" }, artifacts: [...saved.artifacts, { id: "memory", kind: "memory", sha256: "memory-sha", payload: { status: "complete", outcome: "私人邀请", position: { current_location: "渡口", recent_major_event: "邀请同行" }, factual_changes: { add_events: [{ summary: "约定同行" }] }, changes: [], diagnostics: [], unresolved: [] } }] };
    const previous = mocks.api.getMockImplementation()!;
    mocks.api.mockImplementation(async (path: string, ...args: unknown[]) => path.endsWith("/batch") ? novel : previous(path, ...args));
    render(<GenerationWorkspace projectId="p" onAdopted={vi.fn()} />);
    await screen.findByDisplayValue("邀请同行");
    expect(screen.getByLabelText("当前地点")).toHaveValue("渡口");
    expect(screen.getByText("预览正式采用")).toBeDisabled();
    fireEvent.click(screen.getByLabelText("我已核对本章实际事实、当前现场和资料变化；缺失或错误项已补充"));
    fireEvent.click(screen.getByText("预览正式采用"));
    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith(expect.stringContaining("adoption-preview"), expect.objectContaining({ body: expect.stringContaining('"facts_confirmed":true') })));
    fireEvent.change(screen.getByLabelText("当前地点"), { target: { value: "码头" } });
    expect(screen.getByText("预览正式采用")).toBeDisabled();
  });
});
