import { useEffect, useState } from "react";

import { activeProjectOperations, type ProjectOperation } from "./projectOperations";
import { api, type LocalTaskResponse } from "./api";
import { startPolling } from "./polling";

export function GlobalRequestStatus({ locallyBusy, projectId }: { locallyBusy: boolean; projectId?: string | null }) {
  const [pendingRequests, setPendingRequests] = useState(0);
  const [projectOperations, setProjectOperations] = useState<ProjectOperation[]>(
    activeProjectOperations,
  );
  const [hidden, setHidden] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [taskState, setTaskState] = useState<{ projectId: string | null; items: LocalTaskResponse[] }>({ projectId: null, items: [] });
  const localTasks = taskState.projectId === projectId ? taskState.items : [];
  const [taskCenterOpen, setTaskCenterOpen] = useState(false);
  const [notificationStatus, setNotificationStatus] = useState<string | null>(null);
  const [systemNotificationsEnabled, setSystemNotificationsEnabled] = useState(
    () => window.localStorage.getItem("novel-writer:system-task-notifications") === "enabled",
  );
  const waiting = locallyBusy || pendingRequests > 0 || projectOperations.length > 0;
  const notifyWhileHidden = systemNotificationsEnabled && typeof Notification !== "undefined" && Notification.permission === "granted";

  useEffect(() => {
    const updatePending = (event: Event) => {
      setPendingRequests((event as CustomEvent<number>).detail);
    };
    const updateProjectOperations = (event: Event) => {
      setProjectOperations((event as CustomEvent<ProjectOperation[]>).detail);
    };
    window.addEventListener("novel-writer:pending", updatePending);
    window.addEventListener("novel-writer:project-operations", updateProjectOperations);
    return () => {
      window.removeEventListener("novel-writer:pending", updatePending);
      window.removeEventListener("novel-writer:project-operations", updateProjectOperations);
    };
  }, []);

  useEffect(() => {
    if (!waiting) {
      setHidden(false);
      setElapsedSeconds(0);
      return;
    }
    setHidden(false);
    const startedAt = Date.now();
    const updateElapsed = () => setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    updateElapsed();
    const timer = window.setInterval(updateElapsed, 1000);
    return () => window.clearInterval(timer);
  }, [waiting]);

  useEffect(() => {
    setNotificationStatus(null);
    if (!projectId) return;
    return startPolling({
      intervalMs: 5000,
      runWhenHidden: notifyWhileHidden,
      poll: async (signal) => {
        const items = await api<LocalTaskResponse[]>(`/api/local-tasks?project_id=${encodeURIComponent(projectId)}&limit=20`, { signal });
        if (!signal.aborted) setTaskState({ projectId, items });
      },
    });
  }, [projectId, notifyWhileHidden]);

  useEffect(() => {
    const completed = localTasks.filter((item) => item.unread && ["completed", "failed"].includes(item.status));
    if (completed.length === 0) return;
    const key = "novel-writer:notified-local-tasks-v1";
    let notified = new Set<string>();
    try { notified = new Set(JSON.parse(window.localStorage.getItem(key) ?? "[]") as string[]); }
    catch { notified = new Set(); }
    const fresh = completed.filter((item) => !notified.has(item.task_id));
    if (fresh.length === 0) return;
    setNotificationStatus(`${fresh.length} 个本地任务已完成或需要处理`);
    if (systemNotificationsEnabled && typeof Notification !== "undefined" && Notification.permission === "granted") {
      for (const task of fresh) new Notification("小说工作台任务更新", { body: `${taskKindLabel(task.kind)}：${taskStatusLabel(task.status)}` });
    }
    for (const task of fresh) notified.add(task.task_id);
    window.localStorage.setItem(key, JSON.stringify([...notified].slice(-500)));
  }, [localTasks, systemNotificationsEnabled]);

  useEffect(() => {
    if (!taskCenterOpen) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setTaskCenterOpen(false); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [taskCenterOpen]);

  async function markRead(taskId: string) {
    const updated = await api<LocalTaskResponse>(`/api/local-tasks/${taskId}/read`, { method: "POST" });
    setTaskState((current) => current.projectId === projectId ? {
      ...current, items: current.items.map((item) => item.task_id === taskId ? updated : item),
    } : current);
  }

  async function enableSystemNotifications() {
    if (typeof Notification === "undefined") {
      setNotificationStatus("当前浏览器不支持系统通知");
      return;
    }
    const permission = await Notification.requestPermission();
    const enabled = permission === "granted";
    window.localStorage.setItem("novel-writer:system-task-notifications", enabled ? "enabled" : "disabled");
    setSystemNotificationsEnabled(enabled);
    setNotificationStatus(enabled ? "已启用系统任务通知" : "系统通知未获授权；应用内通知仍会保留");
  }

  if (!projectId && (!waiting || hidden)) return null;

  return (
    <>
    {projectId && <div className="task-center"><button type="button" aria-expanded={taskCenterOpen} onClick={() => setTaskCenterOpen((value) => !value)}>任务中心{localTasks.some((item) => item.unread) ? `（${localTasks.filter((item) => item.unread).length}）` : ""}</button>{taskCenterOpen && <section className="task-center-panel" aria-label="本地任务中心"><header><h2>任务中心</h2><button type="button" aria-label="关闭任务中心" onClick={() => setTaskCenterOpen(false)}>×</button></header>{localTasks.length === 0 ? <p>当前没有本地长任务。</p> : <ul>{localTasks.map((task) => <li key={task.task_id}><strong>{taskKindLabel(task.kind)}</strong><span>{taskStatusLabel(task.status)} · {task.progress}%{task.error_code ? ` · ${task.error_code}` : ""}</span><div className="button-row">{task.status === "completed" && task.output_sha256 && <a href={`/backend/api/local-tasks/${task.task_id}/artifact`} download>下载工件</a>}{task.unread && <button type="button" onClick={() => void markRead(task.task_id).catch(() => setNotificationStatus("标记已读失败"))}>标记已读</button>}</div></li>)}</ul>}<button type="button" onClick={() => void enableSystemNotifications()}>{systemNotificationsEnabled ? "系统通知已启用" : "启用系统通知"}</button></section>}</div>}
    {notificationStatus && <div className="task-notification" role="status" aria-live="polite">{notificationStatus}<button type="button" aria-label="关闭任务通知" onClick={() => setNotificationStatus(null)}>×</button></div>}
    {waiting && !hidden && <div className="global-wait" role="status" aria-live="polite">
      <div className="wait-spinner" />
      <strong>{projectOperations.length > 0
        ? `${projectOperations.length} 部小说正在独立处理`
        : pendingRequests > 1 ? `${pendingRequests} 个后台操作正在处理` : "后台操作正在处理"}</strong>
      {projectOperations.length > 0 ? (
        <ul className="project-operation-list">
          {projectOperations.map((operation) => (
            <li key={operation.id}>
              <b>《{operation.projectTitle}》</b>
              <span>现在正在进行 {operation.stage}</span>
              {operation.status === "queued" && <em>已入队，等待后台执行</em>}
              {operation.status === "stopping" && <em>正在安全停止</em>}
              {operation.status === "reconciling" && <em>正在与后端账本核对结果</em>}
              {operation.detail && <small>{operation.detail}</small>}
              <small>已等待 {Math.max(0, Math.floor((Date.now() - operation.startedAt) / 1000))} 秒</small>
            </li>
          ))}
        </ul>
      ) : (
        <span>已等待 {elapsedSeconds} 秒。当前操作所属作品请勿重复点击；其他作品仍可切换并执行自己的下一步。</span>
      )}
      {projectOperations.length > 0 && <span>每部小说拥有独立锁；同一小说不会并发重复执行，其他小说仍可继续。</span>}
      {elapsedSeconds >= 15 && (
        <button type="button" onClick={() => setHidden(true)}>
          隐藏后台提示
        </button>
      )}
    </div>}
    </>
  );
}

function taskKindLabel(kind: string): string {
  return ({ import: "导入正文", export: "导出作品", export_markdown: "导出 Markdown", export_txt: "导出 TXT", export_docx: "导出 DOCX", export_epub: "导出 EPUB", project_backup: "创建作品备份", search_rebuild: "重建搜索", archive: "生成封存包", maintenance: "维护检查" } as Record<string, string>)[kind] ?? kind;
}

function taskStatusLabel(status: string): string {
  return ({ queued: "排队中", running: "执行中", completed: "已完成", failed: "失败" } as Record<string, string>)[status] ?? status;
}
