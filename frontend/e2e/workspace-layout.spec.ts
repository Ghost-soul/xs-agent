import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const projectId = "10000000-0000-0000-0000-000000000001";
const projectTitle = "雾海来信：在漫长旅途中寻找失落的群星";
async function installLayoutFixture(page: Page) {
  const unexpectedRequests: string[] = [];
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.route("**/backend/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/backend/, "");
    if (request.method() !== "GET") {
      unexpectedRequests.push(`${request.method()} ${path}`);
      await route.fulfill({ status: 403, json: { detail: "Read-only layout fixture" } });
      return;
    }
    let payload: unknown;
    if (path === "/health") payload = { status: "ok", database: "ok" };
    else if (path === "/api/projects") payload = Array.from({ length: 6 }, (_, index) => ({
      project_id: index === 0 ? projectId : `20000000-0000-0000-0000-00000000000${index}`,
      title: index === 0 ? projectTitle : `作品 ${index} · 山海之间`,
      current_version: 18, created_at: "2026-09-01T00:00:00Z",
    }));
    else if (path.endsWith("/chapters")) payload = Array.from({ length: 80 }, (_, index) => ({
      chapter_id: `chapter-${index + 1}`, project_id: projectId, ordinal: index + 1,
      title: `长夜之后，第 ${index + 1} 封来自远方的信`, current_version: 18,
      phase: "committed", next_action: null, revision_id: `revision-${index + 1}`,
    }));
    else if (path === "/api/search/status") payload = { status: "ready" };
    else if (path.endsWith("/reader/manifest")) payload = {
      project_id: projectId, title: projectTitle, formal_version: 18,
      chapters: [{ chapter_id: "chapter-80", revision_id: "revision-80", ordinal: 80,
        title: "长夜之后，第 80 封来自远方的信", char_count: 20, body_sha256: "a".repeat(64),
        body: "这是一段只用于界面验证的虚构正文。" }],
    };
    else if (path.endsWith("/workspace")) payload = {
      path: "F:/fixture", exists: false, formal_version: 18, session_count: 0,
      chunk_count: 0, last_synced_at: null, synced_version: null, up_to_date: false, policy: "fixture",
    };
    else if (["/api/projects/archived", "/api/local-tasks", "/api/provider-profiles"].includes(path)
      || path.endsWith("/versions") || path.endsWith("/bookmarks")) payload = [];
    else {
      unexpectedRequests.push(`${request.method()} ${path}`);
      await route.fulfill({ status: 404, json: { detail: "Unknown fixture route" } });
      return;
    }
    await route.fulfill({ status: 200, json: payload });
  });
  return { unexpectedRequests, pageErrors };
}

async function expectNoPageOverflow(page: Page) {
  const widths = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(widths.content).toBeLessThanOrEqual(widths.viewport + 1);
}

test("workspace tools stay separate and the directory is accessible without writes", async ({ page }, testInfo) => {
  const audit = await installLayoutFixture(page);
  await page.goto(`/projects/${projectId}/data`);
  await expect(page.getByRole("heading", { name: projectTitle })).toBeVisible();
  await expect(page.getByRole("heading", { name: "项目文件夹、导入导出与备份" })).toBeVisible();
  await expect(page.getByRole("button", { name: "导入与导出", exact: true })).toHaveAttribute("aria-current", "page");
  const search = page.getByLabel("搜索正文和故事资料");
  const tasks = page.getByRole("button", { name: "任务中心", exact: true });
  const searchBox = await search.boundingBox();
  const taskBox = await tasks.boundingBox();
  expect(searchBox).not.toBeNull();
  expect(taskBox).not.toBeNull();
  if (searchBox && taskBox) expect(
    searchBox.x + searchBox.width <= taskBox.x
    || searchBox.y >= taskBox.y + taskBox.height
    || taskBox.y >= searchBox.y + searchBox.height,
  ).toBe(true);
  await tasks.click();
  const taskPanel = page.getByRole("region", { name: "本地任务中心" });
  await expect(taskPanel).toBeVisible();
  const panelBox = await taskPanel.boundingBox();
  expect(panelBox!.x).toBeGreaterThanOrEqual(0);
  expect(panelBox!.x + panelBox!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  await page.getByRole("button", { name: "关闭任务中心" }).click();

  const directory = page.getByRole("region", { name: "章节目录" });
  await expect(directory).toBeHidden();
  await page.getByRole("button", { name: "章节目录 80", exact: true }).click();
  await expect(directory).toBeVisible();
  await expect(page.getByRole("button", { name: "关闭章节目录" })).toBeFocused();
  const lastChapter = directory.getByRole("button", { name: /第 80 封/ });
  await lastChapter.scrollIntoViewIfNeeded();
  await expect(lastChapter).toBeInViewport();
  await expectNoPageOverflow(page);
  await page.getByRole("button", { name: "关闭章节目录" }).focus();
  await page.keyboard.press("Escape");
  await expect(directory).toBeHidden();
  await expect(page.getByRole("button", { name: "章节目录 80", exact: true })).toBeFocused();
  await page.getByRole("button", { name: "章节目录 80", exact: true }).click();
  await directory.getByRole("button", { name: "改标题", exact: true }).click();
  await page.getByRole("textbox", { name: "第1章正式标题", exact: true }).fill("尚未保存的本地标题");
  await page.getByRole("button", { name: "关闭章节目录" }).click();
  await page.getByRole("button", { name: "章节目录 80", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "第1章正式标题", exact: true })).toHaveValue("尚未保存的本地标题");
  await directory.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "关闭章节目录" }).click();

  if (testInfo.project.name === "mobile-390") {
    await expect(page.getByRole("button", { name: `${projectTitle} v18`, exact: true })).toBeHidden();
    await page.getByRole("button", { name: "作品书架" }).click();
  }
  await page.getByRole("textbox", { name: "搜索作品", exact: true }).fill("雾海");
  await expect(page.getByRole("button", { name: `${projectTitle} v18`, exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "作品 1 · 山海之间 v18", exact: true })).toBeHidden();
  if (testInfo.project.name === "mobile-390") await page.getByRole("button", { name: "收起书架" }).click();
  await page.getByRole("button", { name: "高级设置", exact: true }).click();
  await expect(page.getByRole("group", { name: "高级工具" })).toBeVisible();
  await page.keyboard.press("ControlOrMeta+K");
  await expect(search).toBeFocused();
  await expectNoPageOverflow(page);
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations.filter((item) => ["critical", "serious"].includes(item.impact ?? ""))).toEqual([]);
  expect(audit).toEqual({ unexpectedRequests: [], pageErrors: [] });
  await page.screenshot({ path: testInfo.outputPath("workspace-layout.png"), fullPage: true });
  if (testInfo.project.name === "desktop-1280") {
    await page.setViewportSize({ width: 820, height: 1180 });
    await page.getByRole("button", { name: "章节目录 80", exact: true }).click();
    await expect(directory).toBeInViewport();
    await expectNoPageOverflow(page);
    await page.screenshot({ path: testInfo.outputPath("tablet-directory.png"), fullPage: true });
    await page.getByRole("button", { name: "关闭章节目录" }).click();
    await expect(directory).toBeHidden();
  }
  await page.getByRole("button", { name: "章节目录 80", exact: true }).click();
  await directory.getByRole("button", { name: /第 80 封/ }).click();
  await expect(page).toHaveURL(/\/read\?chapter=chapter-80$/);
  await expect(directory).toBeHidden();
  await expect(page.getByRole("heading", { name: "长夜之后，第 80 封来自远方的信" })).toBeVisible();
  await expectNoPageOverflow(page);
  expect(audit).toEqual({ unexpectedRequests: [], pageErrors: [] });
});

