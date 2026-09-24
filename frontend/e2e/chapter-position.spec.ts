import { expect, test } from "@playwright/test";

for (const reference of [true, false]) test(`chapter position ${reference ? "reference" : "missing"} stays reviewable`, async ({ page }, testInfo) => {
  const body = "她在渡口交出了船票。\n\n她拉起披风，仍留在渡口。\n\n两人随后离开，来到城门。";
  const cut = body.indexOf("两人随后");
  const position = { current_location: "渡口", recent_major_event: "交出船票" };
  const mutations: { path: string; data: Record<string, unknown> }[] = [];
  const unexpected: string[] = [];
  const spec = { workflow: "novel-run-v1", stage_mode: "longform-v1", unit_limit: 3, chapter_count: 2, base_version_id: "v", focus_card_id: "girls_love_gl", direction: "人物选择影响后果", relationship_scope: "genre-led", character_selection: "chief-auto-v1", profile_id: "fixture", chief_model: "m", writer_model: "m", input_limit: 100000, chief_output_limit: 100000, writer_output_limit: 100000, auxiliary_output_limit: 100000, max_cost_cny: "10", target_characters: 4000 };
  await page.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/backend", "");
    let value: unknown = [];
    if (route.request().method() !== "GET") {
      const data = route.request().postDataJSON();
      mutations.push({ path, data });
      if (path.endsWith("/stage-adoption-preview")) value = { preview_sha256: "preview", chapters: [{}], full_stage: false, proposal_eligible: false };
      else { unexpected.push(path); await route.fulfill({ status: 403 }); return; }
    } else if (path === "/health") value = { status: "ok" };
    else if (path === "/api/projects") value = [{ project_id: "p", title: "章节现场验证", current_version: 1, created_at: "2026-09-23T00:00:00Z" }];
    else if (path === "/api/search/status") value = { status: "ready" };
    else if (path === "/api/provider-profiles") value = [{ id: "fixture", display_name: "替身", enabled: true, allow_story_data: true, credential_required: false, default_model: "m", models: [{ id: "m" }] }];
    else if (path.endsWith("/setup")) value = { configuration_revision: "author-intent-v1", base_version_id: "v", version: 1, characters: [], narrative_position: {}, style: { selection_mode: "specified", genre_card_id: "girls_love_gl", secondary_genre_card_ids: [], matched_cards: [{ id: "girls_love_gl", name: "百合" }] } };
    else if (path.endsWith("/generation-batches")) value = [{ id: "batch", direction: spec.direction, status: "needs_attention" }];
    else if (path.endsWith("/generation-batches/batch")) value = { id: "batch", project_id: "p", revision: "genre-led-longform-v1", status: "needs_attention", spec, next_action: null, preview_sha256: "original", snapshot: { blockers: [], maximum_calls: 8, normal_calls: 8, maximum_cost_cny: "8" }, state: { candidate_id: "candidate", segments_id: "segments", units_id: "units", memory_id: "memory" }, artifacts: [{ id: "candidate", kind: "candidate", sha256: "candidate", payload: { body, complete: true } }, { id: "units", kind: "units", payload: { items: [{ ordinal: 1, start: 0, end: body.length, memory_id: "handoff" }] } }, { id: "memory", kind: "memory", payload: { status: "partial", outcome: "重建事实", changes: [], diagnostics: [] } }], calls: [] };
    else if (path.endsWith("/stage-chapters")) value = { candidate_sha256: "candidate", manifest_sha256: "segments", tail: null, deferred_fact_count: 0, chapters: [{ id: "c103", number: 1, ordinal: 103, start: 0, end: cut, position: reference ? position : null, position_status: reference ? "reference" : "author-required", position_source: reference ? { end: 10, remaining_characters: cut - 10 } : null, factual_changes: {}, observations: [], diagnostics: [], facts_status: "partial" }, { id: "c104", number: 2, ordinal: 104, start: cut, end: body.length, position: { current_location: "城门", recent_major_event: "抵达城门" }, position_status: "known", factual_changes: {}, observations: [], diagnostics: [], facts_status: "partial" }] };
    else if (!["/api/projects/archived", "/api/local-tasks"].includes(path) && !path.endsWith("/chapters") && !path.endsWith("/versions")) unexpected.push(path);
    await route.fulfill({ status: 200, json: value });
  });
  await page.goto("/projects/p/create");
  await page.getByRole("tab", { name: "审核与采用", exact: true }).click();
  await page.getByLabel("本次采用范围").selectOption("1");
  const chapter = page.getByRole("group", { name: /第 103 章/ });
  if (reference) {
    await expect(chapter.getByLabel("章末地点")).toHaveValue("渡口");
    await expect(chapter.getByText(/参考之后至章末还有/)).toBeVisible();
  } else {
    await expect(chapter.getByText(/第 103 章还需补充：章末地点、本章实际事件/)).toBeVisible();
    await chapter.getByLabel("已核对本章事实和现场，没有带入后章结果").check();
    await expect(page.getByRole("button", { name: "预览阶段采用", exact: true })).toBeDisabled();
    expect(mutations).toEqual([]);
    await chapter.getByLabel("章末地点").fill("渡口");
    await chapter.getByLabel("本章实际事件").fill("交出船票");
  }
  await chapter.screenshot({ path: testInfo.outputPath("chapter-position.png") });
  await chapter.getByLabel("已核对本章事实和现场，没有带入后章结果").check();
  await page.getByRole("button", { name: "预览阶段采用", exact: true }).click();
  await expect(page.getByRole("button", { name: "确认采用阶段并创建正式版本" })).toBeDisabled();
  expect(mutations).toHaveLength(1);
  expect(mutations[0].data.chapters).toMatchObject([{ chapter_id: "c103", narrative_position: position }]);
  expect(unexpected).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);
});
