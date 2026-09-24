import { expect, test } from "@playwright/test";

test("delegate a legacy plot choice and continue existing work with one command", async ({ page }, testInfo) => {
  const posts: { path: string; body: Record<string, unknown> }[] = [];
  const unexpected: string[] = [];
  const plan = { chapter_goal: "让主动选择推动相处", bridge: "接续相见", scenes: [{ event: "约定同行", choice_and_response: "她答应邀请", consequence: "改变路线", focus_percent: 80, transition_percent: 10, other_percent: 10 }], questions: ["谁先表达在意？"] };
  const spec = { stage_mode: "longform-v1", unit_limit: 2, chapter_count: 1, workflow: "novel-run-v1", automation_policy: "legacy-v1", pause_after_plan: true, base_version_id: "v", focus_card_id: "girls_love_gl", direction: "约定同行", relationship_scope: "genre-led", character_selection: "chief-auto-v1", character_ids: [], profile_id: "fixture", chief_model: "m", writer_model: "m", input_limit: 58000, chief_output_limit: 24000, writer_output_limit: 12000, max_cost_cny: "10" };
  let continued = false;
  const detail = () => ({ id: "b", spec, project_id: "p", status: continued ? "running" : "awaiting_plan", next_action: continued ? "write:1" : null, revision: "genre-led-longform-v1", preview_sha256: "preview-sha", snapshot: { maximum_calls: 10, maximum_cost_cny: "5", blockers: [] }, state: { plan_id: "plan", questions_id: "questions" }, artifacts: [{ id: "plan", sha256: "plan-sha", payload: plan }, { id: "questions", sha256: continued ? "answered-sha" : "questions-sha", payload: { items: [{ question: "谁先表达在意？", status: continued ? "answered" : "pending", scope: "current_unit" }] } }], calls: [{ id: "c", action: "plan", status: "completed", actual_cost_cny: "0.25" }, ...(continued ? [{ id: "w", action: "write:1", status: "executing", started_at: new Date().toISOString() }] : [])] });
  await page.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/backend/, "");
    let value: unknown = [];
    if (route.request().method() === "POST") {
      posts.push({ path, body: route.request().postDataJSON() });
      if (path !== "/api/projects/p/generation-batches/b/continue-stage") { unexpected.push(path); await route.fulfill({ status: 403 }); return; }
      continued = true; value = detail();
    } else if (path === "/health") value = { status: "ok" };
    else if (path === "/api/projects") value = [{ project_id: "p", title: "自动衔接验证作品", current_version: 1, created_at: "2026-09-22T00:00:00Z" }];
    else if (path === "/api/search/status") value = { status: "ready" };
    else if (path === "/api/provider-profiles") value = [{ id: "fixture", display_name: "离线模型", enabled: true, allow_story_data: true, credential_required: false, default_model: "m", models: [{ id: "m" }] }];
    else if (path.endsWith("/generation-batches/setup")) value = { configuration_revision: "author-intent-v1", context_budget_revision: "focused-v1", output_budget_revision: "chief-output-v1", automation_revision: "stage-auto-v1", base_version_id: "v", version: 1, characters: [], narrative_position: {}, style: { selection_mode: "specified", genre_card_id: "girls_love_gl", secondary_genre_card_ids: [], matched_cards: [{ id: "girls_love_gl", name: "百合" }] } };
    else if (path.endsWith("/generation-batches")) value = [{ id: "b", direction: spec.direction, status: "awaiting_plan" }];
    else if (path.endsWith("/generation-batches/b")) value = detail();
    else if (!["/api/projects/archived", "/api/local-tasks"].includes(path) && !path.endsWith("/chapters") && !path.endsWith("/versions")) unexpected.push(path);
    await route.fulfill({ status: 200, json: value });
  });
  await page.goto("/projects/p/create");
  const panel = page.getByRole("heading", { name: "确认后自动衔接至审核" }).locator("..");
  await expect(panel).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("workflow-waiting.png"), fullPage: true });
  await expect(panel.getByRole("button")).toBeDisabled();
  await panel.getByLabel("此项是普通剧情选择，交给 Chief／Writer 自主决定").check();
  await panel.getByLabel("确认上述答复／委托，自动执行原费用上限内的剩余步骤并停在作者审核").check();
  await panel.screenshot({ path: testInfo.outputPath("stage-continuation.png") });
  await panel.getByRole("button", { name: "确认并自动继续至审核" }).click();
  await expect(page.getByRole("heading", { name: "正在进行：Writer 写第 1 单元" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("workflow-running.png"), fullPage: true });
  expect(posts).toHaveLength(1);
  expect(posts[0].body).toMatchObject({ expected_plan_sha256: "plan-sha", expected_questions_sha256: "questions-sha", preview_sha256: "preview-sha", delegated_questions: ["谁先表达在意？"] });
  expect(unexpected).toEqual([]);
  const width = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  expect(width[0]).toBeLessThanOrEqual(width[1] + 1);
});
