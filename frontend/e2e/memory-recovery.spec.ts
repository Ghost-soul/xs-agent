import { expect, test } from "@playwright/test";

for (const amending of [false, true]) {
test(`recover truncated Memory in ${amending ? "amendment" : "stage"} scope with explicit cost confirmation`, async ({ page }, testInfo) => {
  const scope = amending ? "本次修订" : "本阶段";
  const action = amending ? "memory_amend" : "memory:2";
  const mutations: string[] = [];
  const unexpected: string[] = [];
  const spec = { stage_mode: "longform-v1", unit_limit: 6, workflow: "novel-run-v1", base_version_id: "v", focus_card_id: "girls_love_gl", direction: "百合推动剧情", relationship_scope: "genre-led", character_selection: "chief-auto-v1", character_ids: [], profile_id: "fixture", chief_model: "m", writer_model: "m", max_cost_cny: "10", input_limit: 58000 };
  const plan = { chapter_goal: "一次邀请改变共同出行", bridge: "接续渡口相遇", major_turn: "决定留下", genre_causal_role: "关系变化影响行程", questions: [], scenes: [{ event: "相邀", choice_and_response: "主动回应", consequence: "改变安排", focus_percent: 100, transition_percent: 0, other_percent: 0 }] };
  let batch = { id: "batch", spec, project_id: "p", status: "needs_attention", revision: "genre-led-longform-v1", next_action: null as string | null, preview_sha256: "preview", plan_edit_revision: "author-plan-v1", memory_recovery_available: true, snapshot: { maximum_calls: 18, blockers: [] }, state: { amendment_authorized_sha256: amending ? "amendment-sha" : null, plan_id: "plan", candidate_id: "body", units_id: "units", input_authorization_id: "input-auth", message: "响应不完整" }, artifacts: [{ id: "plan", kind: "plan", sha256: "plan-sha", payload: plan }, { id: "body", kind: "candidate", sha256: "body-sha", payload: { body: "第一单元：林青递出船票。\n\n第二单元：江月答应同行。", complete: true } }, { id: "units", kind: "units", sha256: "units-sha", payload: { items: [{ complete: true, memory_id: "memory1" }, { complete: true }] } }], calls: [{ id: "failed", action, status: "local_failure", started_at: "2026-09-22T12:45:00Z", actual_cost_cny: "0.125055", diagnostic: { code: "output_limit_exceeded", message: "本次达到输出上限 6000 tokens，响应被截断。", visible_characters: 11145 }, can_revalidate: false, revalidation_blocker: "输出已被截断，本地重验无法补齐缺失内容。" }] };
  await page.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/backend/, "");
    const method = route.request().method(); let value: unknown = [];
    if (method !== "GET") {
      mutations.push(path);
      if (path.endsWith("/memory-recovery-authorize") && method === "POST") {
        expect(route.request().postDataJSON()).toEqual({ output_limit: 100000, all_roles: true, max_cost_cny: "20", preview_sha256: "recovery-preview", confirmed: true });
        batch = { ...batch, status: "running", next_action: action, memory_recovery_available: false, calls: [...batch.calls, { ...batch.calls[0], id: "replacement", status: "executing", started_at: new Date().toISOString() }] }; value = batch;
      } else { unexpected.push(`${method} ${path}`); await route.fulfill({ status: 403 }); return; }
    } else if (path === "/health") value = { status: "ok" };
    else if (path === "/api/projects") value = [{ project_id: "p", title: "Memory 恢复验证", current_version: 1, created_at: "2026-09-22T00:00:00Z" }];
    else if (path === "/api/search/status") value = { status: "ready" };
    else if (path === "/api/provider-profiles") value = [{ id: "fixture", display_name: "离线模型", enabled: true, allow_story_data: true, credential_required: false, default_model: "m", models: [{ id: "m" }] }];
    else if (path.endsWith("/setup")) value = { configuration_revision: "author-intent-v1", context_budget_revision: "focused-v1", output_budget_revision: "chief-output-v1", automation_revision: "stage-auto-v1", base_version_id: "v", version: 1, characters: [], narrative_position: {}, style: { selection_mode: "specified", genre_card_id: "girls_love_gl", secondary_genre_card_ids: [], matched_cards: [{ id: "girls_love_gl", name: "百合" }] } };
    else if (path.endsWith("/generation-batches")) value = [{ id: "batch", status: batch.status, direction: spec.direction }];
    else if (path.endsWith("/generation-batches/batch")) value = batch;
    else if (path.endsWith("/memory-recovery-preview")) value = { preview_sha256: "recovery-preview", action, previous_output_limit: 6000, output_limit: 100000, input_limit: 200000, input_tokens: 30390, spent_cost_cny: "1.451187", remaining_cost_upper_cny: "14.4", total_cost_upper_cny: "15.851187", previous_max_cost_cny: "10", max_cost_cny: new URL(route.request().url()).searchParams.get("max_cost_cny") ?? "10", all_roles: true, blockers: new URL(route.request().url()).searchParams.get("max_cost_cny") === "20" ? [] : ["超过原阶段预算，请调整预算后重新核算"] };
    else if (!["/api/projects/archived", "/api/local-tasks"].includes(path) && !path.endsWith("/chapters") && !path.endsWith("/versions")) unexpected.push(path);
    await route.fulfill({ status: 200, json: value });
  });
  await page.goto("/projects/p/create");
  await expect(page.getByRole("heading", { name: amending ? "补全 Memory 并继续修订核验" : "补全 Memory 并继续阶段" })).toBeVisible();
  await expect(page.getByText("超过原阶段预算，请调整预算后重新核算")).toBeVisible();
  await expect(page.getByRole("button", { name: "确认补全 Memory 并继续" })).toHaveCount(0);
  expect(mutations).toEqual([]);
  await page.getByLabel(`${scope}总费用上限（含已用费用，元）`).fill("20");
  await page.getByRole("button", { name: "核算 Memory 补全与剩余费用（不调用模型）" }).click();
  await expect(page.getByRole("button", { name: "确认补全 Memory 并继续" })).toBeDisabled();
  await expect(page.getByLabel("正文", { exact: true })).toHaveValue(/第二单元：江月答应同行/);
  await expect(page.getByText(/30,390 \/ 200,000/)).toBeVisible();
  expect(mutations).toEqual([]);
  await page.getByRole("tab", { name: "调用详情" }).click();
  await expect(page.getByRole("button", { name: "纯本地重验" })).toHaveCount(0);
  await expect(page.getByText("输出已被截断，本地重验无法补齐缺失内容。", { exact: true })).toBeVisible();
  await page.getByRole("region", { name: "Memory 补全与费用" }).screenshot({ path: testInfo.outputPath("memory-recovery.png") });
  await page.getByLabel(`确认一次额外 Memory 调用、所有剩余角色额度、资料外发范围与${scope}总预算`).check();
  await page.getByRole("button", { name: "确认补全 Memory 并继续" }).click();
  await expect(page.getByRole("heading", { name: amending ? "正在进行：Memory 修订后提取" : "正在进行：Memory 提取第 2 单元事实" })).toBeVisible();
  expect(mutations).toEqual(["/api/projects/p/generation-batches/batch/memory-recovery-authorize"]);
  expect(unexpected).toEqual([]);
  const widths = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  expect(widths[0]).toBeLessThanOrEqual(widths[1] + 1);
});

}
