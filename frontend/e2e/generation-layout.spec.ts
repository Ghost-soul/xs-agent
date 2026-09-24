import { expect, test } from "@playwright/test";

test("stage guidance keeps review, running, blocked and unknown actions separate", async ({ page }, testInfo) => {
  const unexpected: string[] = [];
  const spec = { stage_mode: "longform-v1", unit_limit: 3, chapter_count: 1, workflow: "novel-run-v1", base_version_id: "v", focus_card_id: "girls_love_gl", direction: "一场私人邀请改变了两人的行动", relationship_scope: "genre-led", character_selection: "chief-auto-v1", character_ids: [], profile_id: "fixture", chief_model: "m", writer_model: "m", max_cost_cny: "10" };
  const records = [{ id: "checking", status: "running" }, { id: "reviewing", status: "needs_attention" }, { id: "uncertain", status: "outcome_uncertain" }, { id: "checkpoint", status: "awaiting_plan" }];
  const plan = { chapter_goal: "爱情改变了决定", bridge: "接续相遇", major_turn: "留下", genre_causal_role: "关系改变行动", opening_focus_percent: 0, scenes: [], questions: [] };
  const body = "林青把自己的船票放在江月的掌心。\n\n江月握住她的手，说她也想一起离开。";
  const detail = (id: string) => ({
    id, spec, project_id: "p", status: records.find((r) => r.id === id)!.status, next_action: id === "checking" ? "checker" : null, revision: "genre-led-longform-v1", preview_sha256: "preview", snapshot: { maximum_cost_cny: "5", blockers: [] },
    state: { plan_id: "plan", candidate_id: "body", units_id: "units", ...(id === "reviewing" ? { review_id: "review" } : {}), ...(id === "checkpoint" ? { checkpoint_author_required: true, chief_comparison_id: "comparison" } : {}) },
    artifacts: [{ id: "plan", sha256: "plan-sha", payload: plan }, { id: "body", sha256: "body-sha", payload: { body, complete: true } }, { id: "units", payload: { items: [{ ordinal: 1, start: 0, end: body.length, memory_id: "memory" }] } }, { id: "review", payload: { outcome: "partial", explanation: "特殊在意影响了行动，回应仍有展开空间。" } }, { id: "comparison", payload: { assessment: "后续事件需要让关系选择改变路线。" } }],
    calls: [{ id: "c", action: id === "checkpoint" ? "chief:1" : id === "reviewing" ? "reader" : "checker", status: id === "checking" ? "executing" : id === "uncertain" ? "outcome_uncertain" : "completed", started_at: new Date().toISOString(), actual_cost_cny: "0.50" }],
  });
  await page.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/backend/, "");
    if (route.request().method() !== "GET") { unexpected.push(path); await route.fulfill({ status: 403 }); return; }
    let value: unknown = [];
    if (path === "/health") value = { status: "ok" };
    else if (path === "/api/projects") value = [{ project_id: "p", title: "阶段导航验证", current_version: 1, created_at: "2026-09-22T00:00:00Z" }];
    else if (path === "/api/search/status") value = { status: "ready" };
    else if (path === "/api/provider-profiles") value = [{ id: "fixture", display_name: "离线模型", enabled: true, allow_story_data: true, credential_required: false, default_model: "m", models: [{ id: "m" }] }];
    else if (path.endsWith("/generation-batches/setup")) value = { configuration_revision: "author-intent-v1", context_budget_revision: "focused-v1", output_budget_revision: "chief-output-v1", automation_revision: "stage-auto-v1", base_version_id: "v", version: 1, characters: [], narrative_position: {}, style: { selection_mode: "specified", genre_card_id: "girls_love_gl", secondary_genre_card_ids: [], matched_cards: [{ id: "girls_love_gl", name: "百合" }] } };
    else if (path.endsWith("/generation-batches")) value = records.map((r) => ({ ...r, direction: r.id }));
    else if (records.some((r) => path.endsWith(`/generation-batches/${r.id}`))) value = detail(path.split("/").at(-1)!);
    else if (!["/api/projects/archived", "/api/local-tasks"].includes(path) && !path.endsWith("/chapters") && !path.endsWith("/versions")) unexpected.push(path);
    await route.fulfill({ status: 200, json: value });
  });
  await page.goto("/projects/p/create");
  const current = page.getByRole("region", { name: "当前阶段与下一步" });
  await expect(current.getByText("正在自动衔接，无需点击继续。", { exact: false })).toBeVisible();
  await expect(page.locator('[aria-current="step"]')).toHaveText("4终稿检查");
  await expect(page.getByRole("button", { name: "确认并自动继续至审核" })).toHaveCount(0);

  await page.getByLabel("创作记录", { exact: true }).selectOption("reviewing");
  await expect(current.getByRole("heading")).toHaveText("等待作者审核：正文与反馈已保存");
  await expect(page.locator('[aria-current="step"]')).toHaveText("5作者审核");
  await expect(page.getByRole("tab", { name: "候选正文", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.screenshot({ path: testInfo.outputPath("review-overview.png"), fullPage: true });
  await current.getByRole("button", { name: "查看反馈与采用", exact: true }).click();
  await expect(page.getByRole("tabpanel", { name: "审核与采用" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "独立阅读：部分呈现" })).toBeVisible();
  await page.getByRole("tab", { name: "故事方案", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "候选正文", exact: true })).toBeFocused();
  await expect(page.getByRole("textbox", { name: "正文", exact: true })).toHaveValue(body);

  await page.getByLabel("创作记录", { exact: true }).selectOption("uncertain");
  await expect(current.getByLabel("核对结论")).toBeVisible();
  await expect(current.getByRole("button", { name: "记录核对结论并关闭未知状态" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "授权并开始阶段创作" })).toHaveCount(0);

  await page.getByLabel("创作记录", { exact: true }).selectOption("checkpoint");
  await current.getByRole("button", { name: "查看对照并调整方案" }).click();
  await expect(page.getByLabel("阶段目标", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "确认并自动继续至审核" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "保存方案（不调用模型）" })).toBeDisabled();
  expect(unexpected).toEqual([]);
  const widths = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  expect(widths[0]).toBeLessThanOrEqual(widths[1] + 1);
});
