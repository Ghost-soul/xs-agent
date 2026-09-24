import { expect, test } from "@playwright/test";

test("resumes the failed Writer only after budget and uncertain outcome confirmation", async ({ page }, testInfo) => {
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  const spec = { workflow: "novel-run-v1", stage_mode: "longform-v1", unit_limit: 5, chapter_count: 2, base_version_id: "v", focus_card_id: "world", narrative_card_ids: [], card_selection_policy: "separate-v1", writing_policy: "guided-v1", narrative_policy: "causal-v1", feedback_policy: "logic-v1", direction: "调查真实身份", relationship_scope: "genre-led", character_selection: "chief-auto-v1", character_ids: [], viewpoint: "", relationship_character_ids: [], profile_id: "fixture", chief_model: "m", writer_model: "m", input_limit: 200000, chief_output_limit: 100000, writer_output_limit: 100000, max_cost_cny: "100" };
  const plan = { chapter_goal: "调查改变选择", bridge: "接续线索", major_turn: "确认新线索", world_context: "", scenes: [1, 2, 3].map((n) => ({ event: `核对线索 ${n}`, character_ids: [], choice_and_response: "选择继续调查", consequence: "获得新线索" })), questions: [] };
  let authorized = false;
  const batch = () => ({
    id: "failed", project_id: "p", spec, status: authorized ? "running" : "needs_attention", step_recovery_available: !authorized,
    revision: "genre-led-longform-v1", preview_sha256: "same-preview", next_action: authorized ? "write:1" : null,
    snapshot: { maximum_cost_cny: "3.6", maximum_calls: 11, blockers: [] },
    state: { plan_id: "p1", questions_id: "q1", message: "供应商网关 HTTP 504，原响应保留" },
    artifacts: [{ id: "p1", kind: "plan", sha256: "plan-sha", payload: plan }, { id: "q1", kind: "questions", sha256: "questions-sha", payload: { items: [] } }],
    calls: [{ id: "chief", action: "plan", status: "completed", can_revalidate: false, actual_cost_cny: "0.031401" }, { id: "writer", action: "write:1", status: "local_failure", can_revalidate: false, actual_cost_cny: null }, ...(authorized ? [{ id: "retry", action: "write:1", status: "executing", actual_cost_cny: null }] : [])],
  });
  await page.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/backend/, "");
    let value: unknown = [];
    if (route.request().method() !== "GET") {
      const body = route.request().postDataJSON(); writes.push({ path, body });
      if (path.endsWith("/step-recovery-authorize") && body.confirmed && body.uncertain_confirmed) {
        authorized = true; value = batch();
      } else { await route.fulfill({ status: 403 }); return; }
    } else if (path === "/health") value = { status: "ok" };
    else if (path === "/api/projects") value = [{ project_id: "p", title: "失败步骤恢复验证", current_version: 1, created_at: "2026-09-24T00:00:00Z" }];
    else if (path === "/api/search/status") value = { status: "ready" };
    else if (path === "/api/provider-profiles") value = [{ id: "fixture", display_name: "离线替身", enabled: true, allow_story_data: true, credential_required: false, default_model: "m", models: [{ id: "m" }] }];
    else if (path.endsWith("/generation-batches/setup")) value = { configuration_revision: "author-intent-v1", context_budget_revision: "focused-v1", output_budget_revision: "chief-output-v1", automation_revision: "stage-auto-v1", base_version_id: "v", version: 1, characters: [], narrative_position: {}, available_cards: [{ id: "world", name: "奇幻", layer: "genre" }], style: { genre_card_id: "world", secondary_genre_card_ids: [], matched_cards: [{ id: "world", name: "奇幻", layer: "genre" }] } };
    else if (path.endsWith("/step-recovery-preview")) value = { preview_sha256: "recovery-preview", failed_call_id: "writer", action: "write:1", model: "grok-4.6", input_limit: 200000, output_limit: 100000, known_cost_cny: "0.031401", unknown_cost_reserve_cny: "0.3", retry_cost_upper_cny: "0.3", remaining_cost_upper_cny: "3.3", total_cost_upper_cny: "3.631401", max_cost_cny: "100", requires_uncertain_confirmation: true, partial_response_characters: 0, blockers: [] };
    else if (path.endsWith("/generation-batches")) value = [{ id: "failed", direction: spec.direction, status: batch().status }];
    else if (path.endsWith("/generation-batches/failed")) value = batch();
    await route.fulfill({ status: 200, json: value });
  });
  await page.goto("/projects/p/create");
  const restore = page.getByRole("button", { name: "确认从失败步骤恢复", exact: true });
  await expect(restore).toBeVisible(); await expect(restore).toBeDisabled();
  await expect(page.getByText(/剧情方案：已保存；已写 0 \/ 3/)).toBeVisible();
  expect(writes).toEqual([]);
  await page.getByLabel(/确认一次额外调用/).check(); await expect(restore).toBeDisabled();
  await page.getByLabel(/我已核查原调用/).check(); await expect(restore).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
  await page.screenshot({ path: testInfo.outputPath("recovery-confirmation.png"), fullPage: true });
  await restore.click();
  await expect(page.getByRole("heading", { name: "正在进行：Writer 写第 1 单元" })).toBeVisible();
  expect(writes).toEqual([{ path: "/api/projects/p/generation-batches/failed/step-recovery-authorize", body: { confirmed: true, uncertain_confirmed: true, max_cost_cny: "100", preview_sha256: "recovery-preview" } }]);
});
