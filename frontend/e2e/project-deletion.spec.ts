import { expect, test, type Page } from "@playwright/test";

const projectId = "10000000-0000-0000-0000-000000000001";
const otherId = "20000000-0000-0000-0000-000000000002";
const title = "删除功能隔离样本";

async function fixture(page: Page, pending = false, archived = false, previewGate?: Promise<void>) {
  let deleted = false;
  let status = pending ? "cleanup_pending" : "completed";
  const writes: string[] = [];
  const receipt = () => ({
    deletion_id: "30000000-0000-0000-0000-000000000003", project_id: projectId,
    status, database_deleted: true, counts: {}, cost_summary: {},
    cleanup_error: status === "cleanup_pending" ? "文件占用，请解除后重试" : null,
    created_at: "2026-09-07T01:00:00Z", completed_at: status === "completed" ? "2026-09-07T01:01:00Z" : null,
  });
  await page.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/backend/, "");
    const method = route.request().method();
    let payload: unknown = [];
    if (method !== "GET") {
      writes.push(`${method} ${path}`);
      if (path === `/api/projects/${projectId}/delete`) {
        expect(route.request().postDataJSON()).toEqual({
          confirmed: true, confirmed_title: title, binding_sha256: "a".repeat(64),
        });
        deleted = true;
        payload = receipt();
      } else if (path.endsWith("/cleanup")) {
        expect(route.request().postDataJSON()).toEqual({ confirmed: true });
        status = "completed";
        payload = receipt();
      } else {
        await route.fulfill({ status: 403, json: { detail: "unapproved fixture write" } });
        return;
      }
    } else if (path === "/health") payload = { status: "ok", database: "ok" };
    else if (path === "/api/projects") payload = [
      ...(!deleted && !archived ? [{ project_id: projectId, title, current_version: 1, created_at: "2026-09-07" }] : []),
      { project_id: otherId, title: "必须保留的小说", current_version: 1, created_at: "2026-09-07" },
    ];
    else if (path === "/api/projects/archived") payload = archived && !deleted
      ? [{ project_id: projectId, title, current_version: 1, archived_at: "2026-09-07" }] : [];
    else if (path === `/api/projects/${projectId}/delete-preview`) payload = {
      project_id: projectId, title, binding_sha256: "a".repeat(64),
      counts: { chapters: 3, state_versions: 5, writing_sessions: 2 },
      file_count: 9, estimated_file_bytes: 4096, shared_files_preserved: 1, cost_summary: {}, blockers: [],
      exclusions: ["共享资源和外部备份不删除", "仅保留不含小说内容的删除凭证和费用摘要"],
    };
    else if (path === "/api/project-deletions") payload = deleted ? [receipt()] : [];
    else if (path === "/api/search/status") payload = { status: "ready" };
    else if (path.endsWith("/workspace")) payload = {
      path: "F:/fixture", exists: false, formal_version: 1, session_count: 0, chunk_count: 0,
      last_synced_at: null, synced_version: null, up_to_date: false, policy: "fixture",
    };
    if (path.endsWith("/delete-preview") && previewGate) await previewGate;
    await route.fulfill({ status: 200, json: payload });
  });
  await page.goto(`/projects/${archived ? otherId : projectId}/data?tab=backup`);
  await expect(page.getByRole("heading", { name: "项目文件夹、导入导出与备份" })).toBeVisible();
  return writes;
}

async function openLibrary(page: Page, mobile: boolean) {
  if (mobile && await page.getByRole("button", { name: "作品书架", exact: true }).isVisible()) {
    await page.getByRole("button", { name: "作品书架", exact: true }).click();
  }
}

test("permanent deletion removes exact-project browser drafts without resurrecting autosaves", async ({ page }, testInfo) => {
  const writes = await fixture(page);
  await page.evaluate(async ({ projectId, otherId }) => {
    const modulePath = "/src/localDrafts.ts";
    const drafts = await import(modulePath);
    for (const identifier of [projectId, otherId, "global"]) {
      await drafts.writeLocalDraft({
        schema_version: "local-draft-v1", draft_key: `blueprint:${identifier}`,
        project_id: identifier, surface: "blueprint", resource_id: identifier,
        base_version: 1, base_sha256: null, payload: `private-body-${identifier}`,
        payload_sha256: "a".repeat(64), updated_at: "2026-09-07T01:00:00Z",
      });
    }
    localStorage.setItem(`novel-writer:reader-position:${projectId}`, "private reading position");
  }, { projectId, otherId });
  await openLibrary(page, testInfo.project.name === "mobile-390");
  await page.getByRole("button", { name: `永久删除小说 ${title}`, exact: true }).click();
  const confirm = page.getByRole("button", { name: "永久删除整本小说", exact: true });
  await expect(confirm).toBeDisabled();
  await page.getByRole("alertdialog").getByRole("textbox").fill("删除功能");
  await expect(confirm).toBeDisabled();
  expect(writes).toEqual([]);
  await page.getByRole("alertdialog").getByRole("textbox").fill(title);
  await page.screenshot({ path: testInfo.outputPath("delete-confirmation.png"), fullPage: true });
  await confirm.click();
  await expect(page.getByText("清理完成", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: `永久删除小说 ${title}`, exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "永久删除小说 必须保留的小说", exact: true })).toBeVisible();
  const remaining = await page.evaluate(async ({ projectId }) => {
    const modulePath = "/src/localDrafts.ts";
    const drafts = await import(modulePath);
    await drafts.writeLocalDraft({
      schema_version: "local-draft-v1", draft_key: `late:${projectId}`, project_id: projectId,
      surface: "blueprint", resource_id: projectId, base_version: 1, base_sha256: null,
      payload: "stale tab must not resurrect this", payload_sha256: "a".repeat(64), updated_at: "2026-09-07",
    });
    return { drafts: await drafts.listLocalDrafts(), position: localStorage.getItem(`novel-writer:reader-position:${projectId}`) };
  }, { projectId });
  expect(remaining.position).toBeNull();
  expect(remaining.drafts.map((item: { project_id: string }) => item.project_id).sort()).toEqual([otherId, "global"].sort());
  expect(writes).toEqual([`POST /api/projects/${projectId}/delete`]);
  const sizes = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  expect(sizes[0]).toBeLessThanOrEqual(sizes[1] + 1);
  await page.screenshot({ path: testInfo.outputPath("delete-completed.png"), fullPage: true });
});

test("pending file cleanup remains recoverable after reloading the shelf", async ({ page }, testInfo) => {
  const writes = await fixture(page, true);
  await openLibrary(page, testInfo.project.name === "mobile-390");
  await page.getByRole("button", { name: `永久删除小说 ${title}`, exact: true }).click();
  await page.getByRole("alertdialog").getByRole("textbox").fill(title);
  await page.getByRole("button", { name: "永久删除整本小说", exact: true }).click();
  await expect(page.getByText("数据库已删除，文件清理未完成", { exact: true })).toBeVisible();
  await page.reload();
  await openLibrary(page, testInfo.project.name === "mobile-390");
  await page.getByRole("button", { name: "删除记录与残留清理", exact: true }).click();
  await page.getByRole("button", { name: "继续清理残留", exact: true }).click();
  expect(writes).toHaveLength(1);
  await page.getByRole("button", { name: "继续本地清理", exact: true }).click();
  await expect(page.getByText("清理完成", { exact: true })).toBeVisible();
  expect(writes).toHaveLength(2);
});

test("an archived novel's slow preview can be cancelled without deleting it", async ({ page }, testInfo) => {
  let releasePreview!: () => void;
  const gate = new Promise<void>((resolve) => { releasePreview = resolve; });
  const writes = await fixture(page, false, true, gate);
  await openLibrary(page, testInfo.project.name === "mobile-390");
  await page.getByRole("button", { name: "归档作品（1）", exact: true }).click();
  const remove = page.getByRole("button", { name: `永久删除小说 ${title}`, exact: true });
  await remove.click();
  await expect(page.getByRole("status").filter({ hasText: "正在盘点数据和共享文件" })).toBeVisible();
  await expect(remove).toBeDisabled();
  await page.getByRole("button", { name: "取消核对", exact: true }).click();
  await expect(remove).toBeEnabled();
  releasePreview();
  await remove.click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("alertdialog").getByRole("button", { name: "取消", exact: true }).click();
  await expect(remove).toBeEnabled();
  expect(writes).toEqual([]);
  await expect(page.getByText(title, { exact: true })).toBeVisible();
  const sizes = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  expect(sizes[0]).toBeLessThanOrEqual(sizes[1] + 1);
});
