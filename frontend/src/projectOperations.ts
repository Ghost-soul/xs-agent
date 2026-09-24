
export type ProjectOperation = {
  id: number;
  projectId: string;
  projectTitle: string;
  stage: string;
  startedAt: number;
  updatedAt: number;
  status: "queued" | "running" | "stopping" | "reconciling";
  source: "frontend" | "backend";
  detail: string | null;
};

const activeOperations = new Map<string, ProjectOperation>();
type RemoteOperationSnapshot = {
  operations: ProjectOperation[];
  lastSeenAt: number;
};

export const PROJECT_OPERATION_HEARTBEAT_MS = 15_000;
export const REMOTE_PROJECT_OPERATION_LEASE_MS = 90_000;

const remoteOperations = new Map<string, RemoteOperationSnapshot>();
const tabId = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Date.now());
const channel = typeof window !== "undefined" && "BroadcastChannel" in window
  ? new BroadcastChannel("novel-writer:project-state")
  : null;
let maintenanceTimer: number | null = null;

function pruneExpiredRemoteOperations(now = Date.now()): boolean {
  let changed = false;
  for (const [source, remote] of remoteOperations) {
    if (now - remote.lastSeenAt < REMOTE_PROJECT_OPERATION_LEASE_MS) continue;
    remoteOperations.delete(source);
    changed = true;
  }
  return changed;
}

function maintainTimer(): void {
  const needed = activeOperations.size > 0 || remoteOperations.size > 0;
  if (!needed && maintenanceTimer !== null) {
    window.clearInterval(maintenanceTimer);
    maintenanceTimer = null;
    return;
  }
  if (!needed || maintenanceTimer !== null) return;
  maintenanceTimer = window.setInterval(() => {
    const remoteChanged = pruneExpiredRemoteOperations();
    if (activeOperations.size > 0) {
      channel?.postMessage({ type: "operations", source: tabId, operations: [...activeOperations.values()] });
    }
    if (remoteChanged) publishLocalSnapshot();
    maintainTimer();
  }, PROJECT_OPERATION_HEARTBEAT_MS);
}

function snapshot(): ProjectOperation[] {
  pruneExpiredRemoteOperations();
  const merged = new Map<string, ProjectOperation>();
  const remote = [...remoteOperations.values()].flatMap((item) => item.operations);
  for (const operation of [...activeOperations.values(), ...remote]) {
    const existing = merged.get(operation.projectId);
    if (!existing || operation.updatedAt > existing.updatedAt) merged.set(operation.projectId, operation);
  }
  return [...merged.values()].sort((left, right) => left.startedAt - right.startedAt);
}

function publishLocalSnapshot() {
  window.dispatchEvent(new CustomEvent("novel-writer:project-operations", { detail: snapshot() }));
  maintainTimer();
}

export function receiveRemoteProjectOperations(
  source: string,
  operations: ProjectOperation[],
  receivedAt = Date.now(),
): void {
  if (operations.length === 0) remoteOperations.delete(source);
  else remoteOperations.set(source, { operations, lastSeenAt: receivedAt });
  publishLocalSnapshot();
}

if (channel) {
  channel.addEventListener("message", (event: MessageEvent) => {
    const message = event.data as { type?: string; source?: string; operations?: ProjectOperation[]; projectId?: string };
    if (!message.source || message.source === tabId) return;
    if (message.type === "state-request") {
      channel.postMessage({ type: "operations", source: tabId, operations: [...activeOperations.values()] });
      return;
    }
    if (message.type === "operations") {
      receiveRemoteProjectOperations(
        message.source,
        Array.isArray(message.operations) ? message.operations : [],
      );
    }
    if (message.type === "project-invalidated" && message.projectId) {
      window.dispatchEvent(new CustomEvent("novel-writer:project-invalidated", { detail: { projectId: message.projectId } }));
    }
  });
  window.addEventListener("beforeunload", () => {
    channel.postMessage({ type: "operations", source: tabId, operations: [] });
  });
  channel.postMessage({ type: "state-request", source: tabId });
}

export function activeProjectOperations(): ProjectOperation[] {
  return snapshot();
}

export function activeProjectOperation(projectId: string): ProjectOperation | null {
  return snapshot().find((item) => item.projectId === projectId) ?? null;
}

export function publishProjectInvalidation(projectId: string, refreshLocal = true): void {
  if (refreshLocal) window.dispatchEvent(new CustomEvent("novel-writer:project-invalidated", { detail: { projectId } }));
  channel?.postMessage({ type: "project-invalidated", source: tabId, projectId });
}

