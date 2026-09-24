export type LocalDraft<T = unknown> = {
  schema_version: "local-draft-v1";
  draft_key: string;
  project_id: string;
  surface: string;
  resource_id: string;
  base_version: number | null;
  base_sha256: string | null;
  payload: T;
  payload_sha256: string;
  updated_at: string;
};

const databaseName = "novel-writer-local-drafts-v1";
const storeName = "drafts";
const deletionMarkerKey = (projectId: string) => `__deleted_project__:${projectId}`;
type ProjectDeletionMarker = {
  schema_version: "project-deletion-local-v1";
  draft_key: string;
  project_id: string;
};

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(databaseName, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(storeName, { keyPath: "draft_key" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("本地草稿存储不可用"));
  });
}

export async function readLocalDraft<T>(draftKey: string): Promise<LocalDraft<T> | null> {
  if (typeof indexedDB === "undefined") return null;
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const request = db.transaction(storeName, "readonly").objectStore(storeName).get(draftKey);
    request.onsuccess = () => resolve((request.result as LocalDraft<T> | undefined) ?? null);
    request.onerror = () => reject(request.error ?? new Error("读取本地草稿失败"));
  });
}

export async function writeLocalDraft<T>(draft: LocalDraft<T>): Promise<void> {
  if (typeof indexedDB === "undefined") return;
  const db = await openDatabase();
  await new Promise<void>((resolve, reject) => {
    const transaction = db.transaction(storeName, "readwrite");
    const store = transaction.objectStore(storeName);
    const marker = store.get(deletionMarkerKey(draft.project_id));
    marker.onsuccess = () => { if (!marker.result) store.put(draft); };
    transaction.oncomplete = () => { db.close(); resolve(); };
    transaction.onabort = () => { db.close(); reject(transaction.error ?? new Error("保存本地草稿失败")); };
  });
}

export async function deleteProjectBrowserData(projectId: string): Promise<void> {
  if (projectId === "global" || !projectId) throw new Error("不能按小说删除全局配置草稿");
  if (typeof indexedDB !== "undefined") {
    const db = await openDatabase();
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction(storeName, "readwrite");
      const store = transaction.objectStore(storeName);
      const marker: ProjectDeletionMarker = {
        schema_version: "project-deletion-local-v1", draft_key: deletionMarkerKey(projectId), project_id: projectId,
      };
      store.put(marker);
      const cursor = store.openCursor();
      cursor.onsuccess = () => {
        const current = cursor.result;
        if (!current) return;
        const value = current.value as LocalDraft | ProjectDeletionMarker;
        if (value.project_id === projectId && value.draft_key !== marker.draft_key) current.delete();
        current.continue();
      };
      transaction.oncomplete = () => { db.close(); resolve(); };
      transaction.onabort = () => { db.close(); reject(transaction.error ?? new Error("本浏览器草稿清理失败")); };
    });
  }
  window.localStorage.removeItem(`novel-writer:reader-position:${projectId}`);
  window.localStorage.removeItem(`novel-writer:preferred-provider:${projectId}`);
  const target = window.sessionStorage.getItem("novel-writer:search-target-v1");
  if (target && (JSON.parse(target) as { project_id?: string }).project_id === projectId) {
    window.sessionStorage.removeItem("novel-writer:search-target-v1");
  }
}

export async function deleteLocalDraft(draftKey: string): Promise<void> {
  if (typeof indexedDB === "undefined") return;
  const db = await openDatabase();
  await new Promise<void>((resolve, reject) => {
    const request = db.transaction(storeName, "readwrite").objectStore(storeName).delete(draftKey);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error ?? new Error("清理本地草稿失败"));
  });
}

export async function listLocalDrafts(projectId?: string): Promise<LocalDraft[]> {
  if (typeof indexedDB === "undefined") return [];
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const request = db.transaction(storeName, "readonly").objectStore(storeName).getAll();
    request.onsuccess = () => {
      const drafts = (request.result as (LocalDraft | ProjectDeletionMarker)[])
        .filter((item): item is LocalDraft => item.schema_version === "local-draft-v1").filter(
        (item) => !projectId || item.project_id === projectId || item.project_id === "global",
      );
      drafts.sort((left, right) => right.updated_at.localeCompare(left.updated_at));
      resolve(drafts);
    };
    request.onerror = () => reject(request.error ?? new Error("读取本地草稿列表失败"));
  });
}

export async function sha256(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, "0")).join("");
}
