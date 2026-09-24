import { describe, expect, it, vi } from "vitest";

import {
  activeProjectOperation,
  PROJECT_OPERATION_HEARTBEAT_MS,
  publishProjectInvalidation,
  receiveRemoteProjectOperations,
  REMOTE_PROJECT_OPERATION_LEASE_MS,
} from "./projectOperations";

describe("project operation registry", () => {
  it("does not request a duplicate local refresh when the caller reloads its saved project", () => {
    const listener = vi.fn();
    window.addEventListener("novel-writer:project-invalidated", listener);
    try {
      publishProjectInvalidation("saved-project", false);
      expect(listener).not.toHaveBeenCalled();
      publishProjectInvalidation("other-project");
      expect(listener).toHaveBeenCalledOnce();
      expect(listener.mock.calls[0][0].detail.projectId).toBe("other-project");
    } finally {
      window.removeEventListener("novel-writer:project-invalidated", listener);
    }
  });

  it("expires a crashed tab's remote operation after its heartbeat lease", () => {
    vi.useFakeTimers({ now: new Date("2026-08-16T00:00:00Z") });
    try {
      const now = Date.now();
      const remoteOperation = {
        id: 91,
        projectId: "remote-project",
        projectTitle: "异常关闭小说",
        stage: "Writer 正文续写阶段",
        startedAt: now,
        updatedAt: now,
        status: "running",
        source: "frontend",
        detail: null,
      } satisfies Parameters<typeof receiveRemoteProjectOperations>[1][number];
      receiveRemoteProjectOperations("crashed-tab", [remoteOperation], now);

      expect(activeProjectOperation("remote-project")).toMatchObject({
        projectTitle: "异常关闭小说",
        status: "running",
      });

      vi.advanceTimersByTime(REMOTE_PROJECT_OPERATION_LEASE_MS - PROJECT_OPERATION_HEARTBEAT_MS);
      expect(activeProjectOperation("remote-project")).not.toBeNull();
      receiveRemoteProjectOperations("crashed-tab", [remoteOperation]);

      vi.advanceTimersByTime(REMOTE_PROJECT_OPERATION_LEASE_MS - PROJECT_OPERATION_HEARTBEAT_MS);
      expect(activeProjectOperation("remote-project")).not.toBeNull();

      vi.advanceTimersByTime(PROJECT_OPERATION_HEARTBEAT_MS);
      expect(activeProjectOperation("remote-project")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});
