import { expect, test } from "@playwright/test";

test("saved short Chief plan recovers locally and waits before writing", async ({ page }, testInfo) => {
  const writes: string[] = [];
  const spec = { workflow: "novel-run-v1", stage_mode: "longform-v1", unit_limit: 5, chapter_count: 2, base_version_id: "v", focus_card_id: "world", narrative_card_ids: [], card_selection_policy: "legacy-v1", writing_policy: "guided-v1", narrative_policy: "causal-v1", feedback_policy: "logic-v1", direction: "调查真实身份", relationship_scope: "genre-led", character_selection: "chief-auto-v1", character_ids: [], viewpoint: "", relationship_character_ids: [], profile_id: "fixture", chief_model: "m", writer_model: "m", input_limit: 200000, chief_output_limit: 100000, writer_output_limit: 100000, max_cost_cny: "10" };
  const plan = { chapter_goal: "调查改变选择", bridge: "接续线索", major_turn: "确认新线索", world_context: "", scenes: [1, 2, 3].map((n) => ({ event: `核对线索 ${n}`, character_ids: [], choice_and_response: "选择继续调查", consequence: "获得新线索" })), questions: [], question_scopes: {}, author_question_reasons: {} };
  let recovered = false;
  const batch = () => ({
    id: "failed", project_id: "p", spec, status: recovered ? "paused" : "needs_attention", revision: "genre-led-longform-v1", preview_sha256: "same-preview", next_action: recovered ? "write:1" : null,
    snapshot: { maximum_cost_cny: "1", maximum_calls: 11, blockers: [] },
    state: recovered ? { plan_id: "p1", questions_id: "q1", message: "Chief 方案已保存，尚未生成本批正文；请核对后继续" } : { message: "已保存响应，Chief 单元设计数量与冻结上限不符" },
    artifacts: recovered ? [{ id: "p1", kind: "plan", sha256: "plan-sha", payload: plan }, { id: "q1", kind: "questions", sha256: "questions-sha", payload: { items: [] } }] : [],
    calls: [{ id: "chief", action: "plan", status: recovered ? "completed" : "local_failure", can_revalidate: !recovered, actual_cost_cny: "0.031401" }],
  });
  await page.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/backend/, "");
    let value: unknown = [];
    if (route.request().method() !== "GET") {
      writes.push(path);
      if (path === "/api/projects/p/generation-batches/failed/calls/chief/revalidate") {
        recovered = true;
        value = { status: "locally_revalidated", provider_requests: "0" };
      } else { await route.fulfill({ status: 403 }); return; }
    } else if (path === "/health") value = { status: "ok" };
    else if (path === "/api/projects") value = [{ project_id: "p", title: "本地恢复验证", current_version: 1, created_at: "2026-09-24T00:00:00Z" }];
    else if (path === "/api/search/status") value = { status: "ready" };
    else if (path === "/api/provider-profiles") value = [{ id: "fixture", display_name: "离线替身", enabled: true, allow_story_data: true, credential_required: false, default_model: "m", models: [{ id: "m" }] }];
    else if (path.endsWith("/generation-batches/setup")) value = { configuration_revision: "author-intent-v1", context_budget_revision: "focused-v1", output_budget_revision: "chief-output-v1", automation_revision: "stage-auto-v1", base_version_id: "v", version: 1, characters: [], narrative_position: {}, available_cards: [{ id: "world", name: "奇幻", layer: "genre" }], style: { genre_card_id: "world", secondary_genre_card_ids: [], matched_cards: [{ id: "world", name: "奇幻", layer: "genre" }] } };
    else if (path.endsWith("/generation-batches")) value = [{ id: "failed", direction: spec.direction, status: batch().status }];
    else if (path.endsWith("/generation-batches/failed")) value = batch();
    await route.fulfill({ status: 200, json: value });
  });
  await page.goto("/projects/p/create");
  await expect(page.getByRole("heading", { name: "已暂停：Chief 设计剧情" })).toBeVisible();
  await expect(page.getByText(/本批次尚未生成正文，原有正式正文保留/)).toBeVisible();
  await page.getByRole("button", { name: "纯本地重验", exact: true }).click();
  await expect(page.getByRole("heading", { name: "已暂停；下一步：Writer 写第 1 单元" })).toBeVisible();
  await expect(page.getByText(/剧情方案：已保存；已写 0 \/ 3/)).toBeVisible();
  await expect(page.getByText(/已记录费用 ¥0.0314/)).toBeVisible();
  expect(writes).toEqual(["/api/projects/p/generation-batches/failed/calls/chief/revalidate"]);
  expect((await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth))).toBeLessThanOrEqual(1);
  await page.screenshot({ path: testInfo.outputPath("recovered-plan.png"), fullPage: true });
});
