import { expect, test } from "@playwright/test";

test("stage scale and local chapter changes remain distinct from adoption", async ({ page }, testInfo) => {
  const body = "𠮷在渡口交出船票。\n\n同行者决定留下。\n\n她们从渡口出发。";
  const paragraphs = [...body.matchAll(/[^\n]+/gu)].map((match, index) => ({ id: `p${index}`, start: [...body.slice(0, match.index)].length, end: [...body.slice(0, match.index! + match[0].length)].length }));
  const size = [...body].length;
  const unexpected: string[] = [], errors: string[] = [];
  const writes: Record<string, unknown>[] = [];
  const spec = { workflow: "novel-run-v1", craft_policy: "stage-craft-v1", stage_scale: { scale_mode: "stage-range", min_characters: 15000, max_characters: 20000, preferred_units: 5 }, stage_mode: "longform-v1", unit_limit: 5, base_version_id: "v", focus_card_id: "fantasy", direction: "完成渡口事件", author_boundaries: "", relationship_scope: "genre-led", character_selection: "chief-auto-v1", character_ids: [], profile_id: "fixture", chief_model: "m", writer_model: "m", input_limit: 200000, chief_output_limit: 100000, writer_output_limit: 100000, auxiliary_output_limit: 100000, max_cost_cny: "10" };
  const batch = { id: "batch", project_id: "p", revision: "genre-led-longform-v1", status: "needs_attention", spec, next_action: null, preview_sha256: "original", passages: paragraphs, snapshot: { blockers: [], maximum_calls: 11, normal_calls: 11, maximum_cost_cny: "8" }, state: { candidate_id: "candidate", segments_id: "segments", units_id: "units", memory_id: "memory", units_finished: true, stage_scale_status: { characters: 8000, status: "below", deficit: 7000, target: spec.stage_scale, units: [] } }, artifacts: [{ id: "candidate", kind: "candidate", sha256: "candidate", payload: { body, complete: true } }, { id: "segments", kind: "segments", sha256: "segments", payload: { segments: [{ end: size }] } }, { id: "units", kind: "units", payload: { items: [] } }, { id: "memory", kind: "memory", payload: { status: "complete", changes: [], diagnostics: [] } }], calls: [] };
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/backend", "");
    let value: unknown = [];
    if (route.request().method() !== "GET") {
      if (!path.endsWith("/chapter-arrangement-preview")) { unexpected.push(path); await route.abort(); return; }
      writes.push(route.request().postDataJSON());
      value = { preview_sha256: "preview", chapters_requiring_position: [1], notice: "章界会使旧标题与采用预览失效。", manifest: { segments: [{}, {}] } };
    } else if (path === "/health") value = { status: "ok" };
    else if (path === "/api/projects") value = [{ project_id: "p", title: "阶段规模隔离夹具", current_version: 1, created_at: "2026-09-27T00:00:00Z" }];
    else if (path === "/api/search/status") value = { status: "ready" };
    else if (path === "/api/provider-profiles") value = [{ id: "fixture", display_name: "离线替身", enabled: true, allow_story_data: true, credential_required: false, default_model: "m", models: [{ id: "m" }] }];
    else if (path.endsWith("/setup")) value = { base_version_id: "v", version: 1, characters: [], narrative_position: {}, style: { selection_mode: "specified", genre_card_id: "fantasy", secondary_genre_card_ids: [], matched_cards: [{ id: "fantasy", name: "奇幻", layer: "genre" }] } };
    else if (path.endsWith("/generation-batches")) value = [{ id: "batch", direction: spec.direction, status: "needs_attention" }];
    else if (path.endsWith("/generation-batches/batch")) value = batch;
    else if (path.endsWith("/stage-chapters")) value = { candidate_sha256: "candidate", manifest_sha256: "segments", tail: null, deferred_fact_count: 0, chapters: [{ id: "c1", number: 1, ordinal: 1, start: 0, end: size, position: { current_location: "渡口", recent_major_event: "出发" }, position_status: "known", factual_changes: {}, observations: [], diagnostics: [], facts_status: "complete" }] };
    else if (!["/api/projects/archived", "/api/local-tasks"].includes(path) && !path.endsWith("/chapters") && !path.endsWith("/versions")) unexpected.push(path);
    await route.fulfill({ status: 200, json: value });
  });
  await page.goto("/projects/p/create");
  await expect(page.getByLabel("阶段篇幅")).toContainText("低于目标 7,000 字");
  await page.getByRole("tab", { name: "审核与采用", exact: true }).click();
  await page.getByText("调整章节：合并相邻章或在自然段末拆分", { exact: true }).click();
  await page.getByLabel(/在第 1 段后分章/).check();
  await page.getByRole("button", { name: "预览新章节" }).click();
  await expect(page.getByText("需要作者填写章末现场：1。本次模型费用 ¥0。")).toBeVisible();
  expect(writes).toEqual([{ candidate_sha256: "candidate", manifest_sha256: "segments", paragraph_ends: ["p0"] }]);
  await page.screenshot({ path: testInfo.outputPath("stage-craft.png"), fullPage: true });
  await page.getByLabel(/在第 1 段后分章/).uncheck();
  await expect(page.getByRole("button", { name: "确认新章界并重新核对逐章事实" })).toHaveCount(0);
  expect(unexpected).toEqual([]); expect(errors).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);
});
