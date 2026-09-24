import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { startPolling } from "./polling";

const stops: Array<() => void> = [];
beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
});
afterEach(() => {
  stops.splice(0).forEach((stop) => stop());
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("visible read polling", () => {
  it("keeps explicit background notification polling active while hidden", async () => {
    vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    const poll = vi.fn(async () => {});
    stops.push(startPolling({ poll, intervalMs: 1000, runWhenHidden: true }));
    await vi.advanceTimersByTimeAsync(1000);
    expect(poll).toHaveBeenCalledTimes(2);
  });

  it("waits for a slow request and coalesces focus events without overlapping", async () => {
    let finish!: () => void;
    const poll = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
    stops.push(startPolling({ poll, intervalMs: 1000 }));
    await vi.advanceTimersByTimeAsync(6000);
    window.dispatchEvent(new Event("focus"));
    window.dispatchEvent(new Event("online"));
    expect(poll).toHaveBeenCalledTimes(1);
    finish();
    await vi.advanceTimersByTimeAsync(999);
    expect(poll).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(poll).toHaveBeenCalledTimes(2);
  });

  it("pauses while hidden, refreshes on return and aborts on disposal", async () => {
    const poll = vi.fn(async (_signal: AbortSignal) => {});
    const stop = startPolling({ poll, intervalMs: 1000 });
    stops.push(stop);
    await vi.advanceTimersByTimeAsync(0);
    vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(8000);
    expect(poll).toHaveBeenCalledTimes(1);
    vi.spyOn(document, "hidden", "get").mockReturnValue(false);
    document.dispatchEvent(new Event("visibilitychange"));
    expect(poll).toHaveBeenCalledTimes(2);
    const signal = poll.mock.calls[1][0];
    stop();
    expect(signal.aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(8000);
    expect(poll).toHaveBeenCalledTimes(2);
  });

  it("cancels a timed-out read, reports it and then resumes at the interval", async () => {
    const poll = vi.fn((signal: AbortSignal) => new Promise<void>((_resolve, reject) => {
      signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    }));
    const onError = vi.fn();
    stops.push(startPolling({ poll, onError, intervalMs: 1000, timeoutMs: 2000 }));
    await vi.advanceTimersByTimeAsync(2000);
    expect(poll.mock.calls[0][0].aborted).toBe(true);
    expect(onError).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(poll).toHaveBeenCalledTimes(2);
  });
});
