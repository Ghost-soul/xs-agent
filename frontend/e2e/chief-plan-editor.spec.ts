import { expect, test } from "@playwright/test";

for (const background of [false, true]) {
test(`edit saved Chief background=${background} and explicitly confirm remaining capacity`, async ({ page }, testInfo) => {
  const mutations: string[] = [];
  const unexpected: string[] = [];
  const legacyPlan = { chapter_goal: "一次私人邀请改变共同出行的决定", bridge: "接续渡口的相遇", major_turn: "江月决定留下", genre_causal_role: "关系变化影响了行程", opening_focus_percent: 0, questions: [], story_questions: ["她会怎样回答？"], author_question_reasons: {}, future_proposal: "同行之后的变化，尚未发生", scenes: [0, 1].map((i) => ({ event: i ? "留下" : "主动邀请", character_ids: ["a", "b"], focus_percent: 50, transition_percent: 0, other_percent: 0, choice_and_response: "林青表达愿望，江月自主回应", consequence: "两人改变了出行安排" })) };
  const { genre_causal_role, opening_focus_percent, ...rest } = legacyPlan;
  const plan = background ? { ...rest, world_context: "渡口夜间闭门，依据正式规则", scenes: rest.scenes.map(({ focus_percent, transition_percent, other_percent, ...scene }) => scene) } : legacyPlan;
  const spec = { writing_policy: background ? "background-v1" : "creative-v1", stage_mode: "longform-v1", unit_limit: 2, workflow: "novel-run-v1", base_version_id: "v", focus_card_id: "girls_love_gl", direction: "百合推动剧情", relationship_scope: "genre-led", character_selection: "chief-auto-v1", character_ids: [], profile_id: "fixture", chief_model: "m", writer_model: "m", max_cost_cny: "10", input_limit: 58000 };
  let batch = { id: "batch", spec, project_id: "p", status: "needs_attention", revision: "genre-led-longform-v1", next_action: null as string | null, preview_sha256: "preview", plan_edit_revision: "author-plan-v1", input_recovery_available: true, snapshot: { maximum_cost_cny: "5", blockers: [], context: { characters: [{ id: "a", name: "林青" }, { id: "b", name: "江月" }] } }, state: { plan_id: "plan", message: "最终输入计数/上界 63120 超过允许值 58000" }, artifacts: [{ id: "plan", kind: "plan", sha256: "plan-sha", payload: plan }], calls: [{ id: "chief", action: "plan", status: "completed", started_at: "2026-09-22T09:14:13Z", actual_cost_cny: "0.48" }] };
  await page.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/backend/, "");
    const method = route.request().method(); let value: unknown = [];
    if (method !== "GET") {
      mutations.push(path);
      if (path.endsWith("/plan") && method === "PUT") {
        const request = route.request().postDataJSON(); expect(request.expected_plan_sha256).toBe("plan-sha");
        batch = { ...batch, state: { plan_id: "author-plan", message: "作者方案已保存" }, artifacts: [...batch.artifacts, { id: "author-plan", kind: "plan", sha256: "author-sha", payload: request.plan }] }; value = batch;
      } else if (path.endsWith("/input-authorize") && method === "POST") {
        expect(route.request().postDataJSON()).toEqual({ input_limit: 200000, output_limit: 100000, all_roles: true, max_cost_cny: "10", preview_sha256: "capacity-preview", confirmed: true });
        batch = { ...batch, status: "running", next_action: "write:1", input_recovery_available: false, calls: [...batch.calls, { id: "writer", action: "write:1", status: "executing", started_at: new Date().toISOString(), actual_cost_cny: "0" }] }; value = batch;
      } else { unexpected.push(`${method} ${path}`); await route.fulfill({ status: 403 }); return; }
    } else if (path === "/health") value = { status: "ok" };
    else if (path === "/api/projects") value = [{ project_id: "p", title: "Chief 方案编辑验证", current_version: 1, created_at: "2026-09-22T00:00:00Z" }];
    else if (path === "/api/search/status") value = { status: "ready" };
    else if (path === "/api/provider-profiles") value = [{ id: "fixture", display_name: "离线模型", enabled: true, allow_story_data: true, credential_required: false, default_model: "m", models: [{ id: "m" }] }];
    else if (path.endsWith("/setup")) value = { configuration_revision: "author-intent-v1", context_budget_revision: "focused-v1", output_budget_revision: "chief-output-v1", automation_revision: "stage-auto-v1", base_version_id: "v", version: 1, characters: [], narrative_position: {}, style: { selection_mode: "specified", genre_card_id: "girls_love_gl", secondary_genre_card_ids: [], matched_cards: [{ id: "girls_love_gl", name: "百合" }] } };
    else if (path.endsWith("/generation-batches")) value = [{ id: "batch", status: batch.status, direction: "百合推动剧情" }];
    else if (path.endsWith("/generation-batches/batch")) value = batch;
    else if (path.endsWith("/input-preview")) value = { preview_sha256: "capacity-preview", output_limit: 100000, all_roles: true, previous_input_limit: 58000, input_limit: 200000, writer_input_tokens: 63200, spent_cost_cny: "0.48", remaining_cost_upper_cny: "4.30", total_cost_upper_cny: "4.78", max_cost_cny: "10", blockers: [] };
    else if (!["/api/projects/archived", "/api/local-tasks"].includes(path) && !path.endsWith("/chapters") && !path.endsWith("/versions")) unexpected.push(path);
    await route.fulfill({ status: 200, json: value });
  });
  await page.goto("/projects/p/create");
  await expect(page.getByLabel("本次输入上限", { exact: true })).toHaveValue("200000");
  await expect(page.getByText(/Chief 方案已保存。核对下方最新输入、全部角色输出额度与费用/)).toBeVisible();
  await expect(page.getByText("最终输入计数/上界 63120 超过允许值 58000", { exact: true })).toBeHidden();
  await expect(page.getByRole("status").filter({ hasText: "新额度预检通过" })).toBeVisible();
  await expect(page.getByRole("button", { name: "确认额度并从 Writer 继续" })).toBeDisabled();
  expect(mutations).toEqual([]);
  await page.getByRole("button", { name: "编辑已保存的 Chief 方案" }).click();
  if (background) { await expect(page.getByLabel("开篇过渡份额（%）")).toHaveCount(0); await expect(page.getByLabel("世界观与背景依据")).toBeVisible(); }
  await page.getByLabel("阶段目标", { exact: true }).fill("让双方主动表达心意，改变接下来的共同选择");
  await page.getByLabel("单元 1 · 事件", { exact: true }).fill("林青在渡口明确发出私人邀请");
  await page.getByLabel("答复与修改说明", { exact: true }).fill("让关系选择直接推动故事");
  await page.screenshot({ path: testInfo.outputPath("chief-plan-edit.png"), fullPage: true });
  await expect(page.getByText("核算剩余输入与费用（不调用模型）")).toBeDisabled();
  await page.getByRole("button", { name: "保存方案（不调用模型）" }).click();
  await expect(page.getByText("已保存新的故事方案版本，Chief 原稿保留；尚未调用模型。")).toBeVisible();
  expect(mutations).toEqual(["/api/projects/p/generation-batches/batch/plan"]);
  await page.getByLabel("查看方案版本").selectOption("plan");
  await expect(page.getByText(/# Chief 故事方案/)).toContainText("一次私人邀请");
  const download = page.waitForEvent("download"); await page.getByText("下载完整方案 JSON").click();
  expect((await download).suggestedFilename()).toContain("Chief方案");
  await expect(page.getByLabel("剩余步骤输入上限", { exact: true })).toHaveValue("200000");
  await expect(page.getByRole("button", { name: "确认额度并从 Writer 继续" })).toBeDisabled();
  await page.screenshot({ path: testInfo.outputPath("capacity-confirmation.png"), fullPage: true });
  await page.getByLabel("确认新的输入、全部角色输出额度、阶段总预算与资料外发范围，继续并停在作者审核").check();
  await page.getByRole("button", { name: "确认额度并从 Writer 继续" }).click();
  await expect(page.getByRole("heading", { name: "正在进行：Writer 写第 1 单元" })).toBeVisible();
  expect(mutations).toHaveLength(2); expect(unexpected).toEqual([]);
  const widths = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  expect(widths[0]).toBeLessThanOrEqual(widths[1] + 1);
});

}
