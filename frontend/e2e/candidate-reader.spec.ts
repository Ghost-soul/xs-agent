import { expect, test } from "@playwright/test";

test("saved prose is readable during a failure; reading and draft previews do not write", async ({ page }, testInfo) => {
  const unexpected: string[] = [];
  const first = "𠮷安推开门，院子里亮着一盏灯。\n\n" + "晚风翻过书页，她终于看清了信上的落款。\n\n".repeat(30);
  const second = "第二天清晨，她带着信走到了渡口。🌙\n\n船还没有开，她决定先去问问摆渡人。";
  const body = `${first}\n\n${second}`;
  const length = (value: string) => Array.from(value).length;
  const records = [{ id: "failed", status: "outcome_uncertain" }, { id: "editable", status: "needs_attention" }, { id: "partial", status: "failed" }, { id: "running", status: "running" }];
  let publishUpdate = false;
  const detail = (id: string) => ({
    id, project_id: "p", status: records.find((r) => r.id === id)!.status, next_action: null,
    spec: { stage_mode: "longform-v1", unit_limit: 3, workflow: "novel-run-v1", base_version_id: "v", focus_card_id: "world", direction: "收到一封信", character_selection: "chief-auto-v1", character_ids: [], profile_id: "fixture", chief_model: "m", writer_model: "m", max_cost_cny: "10" },
    revision: "genre-led-longform-v1", preview_sha256: "preview", snapshot: { maximum_cost_cny: "5", blockers: [] },
    state: { candidate_id: "candidate", units_id: "units" },
    artifacts: [
      { id: "candidate", kind: "candidate", sha256: "body-sha", payload: { body, complete: id !== "partial" } },
      { id: "units", kind: "units", payload: { items: [
        { ordinal: 1, start: 0, end: length(first), complete: true, memory_id: "m1" },
        { ordinal: 2, start: length(first) + 2, end: length(body), complete: id !== "partial" },
      ] } },
    ],
    calls: [{ id: "c", action: "checker", status: "outcome_uncertain", started_at: "2026-09-24T04:57:00Z", actual_cost_cny: null }],
  });
  await page.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/backend/, "");
    if (route.request().method() !== "GET") { unexpected.push(`${route.request().method()} ${path}`); await route.fulfill({ status: 403 }); return; }
    let value: unknown = [];
    if (path === "/health") value = { status: "ok" };
    else if (path === "/api/projects") value = [{ project_id: "p", title: "候选稿阅读验证", current_version: 1, created_at: "2026-09-24T00:00:00Z" }];
    else if (path === "/api/search/status") value = { status: "ready" };
    else if (path === "/api/provider-profiles") value = [{ id: "fixture", display_name: "离线模型", enabled: true, allow_story_data: true, credential_required: false, default_model: "m", models: [{ id: "m" }] }];
    else if (path.endsWith("/generation-batches/setup")) value = { configuration_revision: "author-intent-v1", context_budget_revision: "focused-v1", output_budget_revision: "chief-output-v1", automation_revision: "stage-auto-v1", base_version_id: "v", version: 1, characters: [], narrative_position: {}, style: { genre_card_id: "world", secondary_genre_card_ids: [], matched_cards: [{ id: "world", name: "奇幻" }] } };
    else if (path.endsWith("/generation-batches")) value = records.map((r) => ({ ...r, direction: r.id }));
    else if (records.some((r) => path.endsWith(`/generation-batches/${r.id}`))) {
      const id = path.split("/").at(-1)!;
      const saved = detail(id);
      if (id === "running" && publishUpdate) {
        saved.state.candidate_id = "updated-candidate";
        saved.artifacts[0] = { id: "updated-candidate", kind: "candidate", sha256: "updated-sha", payload: { body: body + "\n\n新保存的后续正文。", complete: true } };
      }
      value = saved;
    }
    else if (!["/api/projects/archived", "/api/local-tasks"].includes(path) && !path.endsWith("/chapters") && !path.endsWith("/versions")) unexpected.push(path);
    await route.fulfill({ status: 200, json: value });
  });
  await page.goto("/projects/p/create");
  const entry = page.getByRole("button", { name: "阅读已写正文", exact: true });
  await expect(entry).toBeVisible();
  await expect(entry).toBeInViewport();
  await entry.click();
  const reader = page.getByRole("dialog", { name: "正文阅读" });
  await expect(reader).toBeVisible();
  await expect(reader.getByRole("button", { name: "返回创作" })).toBeFocused();
  expect(await reader.locator(".candidate-reader-prose").textContent()).toBe(body);
  await reader.getByRole("button", { name: "增大字号" }).click();
  await expect(reader.locator(".candidate-reader-prose")).toHaveCSS("font-size", "22px");
  await reader.getByRole("button", { name: "夜间阅读" }).click();
  await expect(reader.getByRole("button", { name: "夜间阅读" })).toHaveAttribute("aria-pressed", "true");
  await reader.getByLabel("阅读范围").selectOption("0");
  expect(await reader.locator(".candidate-reader-prose").textContent()).toBe(first);
  await reader.getByRole("button", { name: "下一单元" }).click();
  expect(await reader.locator(".candidate-reader-prose").textContent()).toBe(second);
  await expect(reader.getByRole("button", { name: "下一单元" })).toBeDisabled();
  await page.screenshot({ path: testInfo.outputPath("reader-dark.png") });
  await reader.getByRole("button", { name: "夜间阅读" }).click();
  await reader.getByRole("button", { name: "上一单元" }).click();
  await page.screenshot({ path: testInfo.outputPath("reader-light.png") });
  const downloadPromise = page.waitForEvent("download");
  await reader.getByRole("button", { name: "下载全文" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("候选正文.txt");
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
  expect(Buffer.concat(chunks).toString("utf-8")).toBe(body);
  const overflow = await reader.evaluate((element) => element.scrollWidth - element.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await page.keyboard.press("Escape");
  await expect(reader).toHaveCount(0);
  await expect(entry).toBeFocused();

  await page.getByLabel("创作记录", { exact: true }).selectOption("partial");
  await entry.click();
  await expect(reader.getByText("本次输出尚未完成，以下为已保存部分。")).toBeVisible();
  await expect(reader.getByRole("option", { name: "叙事单元 2（未完成）" })).toHaveCount(1);
  await reader.getByRole("button", { name: "返回创作" }).click();

  await page.getByLabel("创作记录", { exact: true }).selectOption("editable");
  await page.getByRole("tab", { name: "候选正文", exact: true }).click();
  const editor = page.getByRole("textbox", { name: "正文", exact: true });
  await editor.fill("作者尚未保存的文字。\n\n新的段落。");
  await page.getByRole("button", { name: "阅读模式", exact: true }).click();
  await expect(reader.getByText("未保存的编辑稿", { exact: true })).toBeVisible();
  await expect(reader.getByLabel("阅读范围")).toHaveCount(0);
  expect(await reader.locator(".candidate-reader-prose").textContent()).toBe("作者尚未保存的文字。\n\n新的段落。");
  await reader.getByRole("button", { name: "返回创作" }).click();
  await expect(editor).toHaveValue("作者尚未保存的文字。\n\n新的段落。");
  await entry.click();
  expect(await reader.locator(".candidate-reader-prose").textContent()).toBe(body);
  await reader.getByRole("button", { name: "返回创作" }).click();
  await page.getByRole("button", { name: "撤销未保存修改", exact: true }).click();
  await page.getByLabel("创作记录", { exact: true }).selectOption("running");
  await entry.click();
  publishUpdate = true;
  await expect(reader.getByText("正文已有更新，返回后重新打开即可阅读新版。")).toBeVisible();
  expect(await reader.locator(".candidate-reader-prose").textContent()).toBe(body);
  await reader.getByRole("button", { name: "返回创作" }).click();
  await entry.click();
  expect(await reader.locator(".candidate-reader-prose").textContent()).toBe(body + "\n\n新保存的后续正文。");
  await expect(reader.getByLabel("阅读范围")).toHaveCount(0);
  expect(unexpected).toEqual([]);
});
