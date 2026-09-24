import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiNetworkError, ApiResponseError, StableWriteOperationKeys, api, jsonBody } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("API connection resilience", () => {
  it("retries a transient read without changing any server state", async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("connection reset"))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api<{ status: string }>("/health")).resolves.toEqual({ status: "ok" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("never retries a write when the connection outcome is unknown", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("connection reset"));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api("/api/projects/project-1/archive", { method: "POST" }))
      .rejects.toMatchObject({ safeToRetry: false, method: "POST" } satisfies Partial<ApiNetworkError>);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("retains a stable write key only while the HTTP outcome is unknown", async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("connection reset"))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: "saved" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: "saved-again" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    vi.stubGlobal("fetch", fetchMock);
    const operations = new StableWriteOperationKeys();
    const path = "/api/projects/project-1/archive";
    const init = { method: "POST", body: jsonBody({ confirmed_title: "作品一", confirmed: true }) };

    await expect(operations.request(path, init)).rejects.toBeInstanceOf(ApiNetworkError);
    await expect(operations.request(path, init)).resolves.toEqual({ status: "saved" });
    await expect(operations.request(path, init)).resolves.toEqual({
      status: "saved-again",
    });

    const keys = fetchMock.mock.calls.map((call) =>
      new Headers((call[1] as RequestInit).headers).get("Idempotency-Key"),
    );
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[1]);
  });

  it("clears a stable write key after a definite HTTP failure", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "conflict" }), {
        status: 409,
        headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: "saved" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    vi.stubGlobal("fetch", fetchMock);
    const operations = new StableWriteOperationKeys();
    const path = "/api/projects/project-1/archive";
    const init = { method: "POST", body: jsonBody({ confirmed_title: "作品一", confirmed: true }) };

    await expect(operations.request(path, init)).rejects.toMatchObject({ status: 409 });
    await expect(operations.request(path, init)).resolves.toEqual({ status: "saved" });

    const firstKey = new Headers(
      (fetchMock.mock.calls[0][1] as RequestInit).headers,
    ).get("Idempotency-Key");
    const secondKey = new Headers(
      (fetchMock.mock.calls[1][1] as RequestInit).headers,
    ).get("Idempotency-Key");
    expect(firstKey).toBeTruthy();
    expect(secondKey).not.toBe(firstKey);
  });

  it.each([new Headers({ "X-Custom": "preserved" }), [["X-Custom", "preserved"]] as [string, string][]])(
    "preserves every supported HeadersInit representation", async (headers) => {
      const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
      vi.stubGlobal("fetch", fetchMock);
      await api("/api/projects", { method: "POST", headers, body: jsonBody({ title: "测试" }) });
      const sent = new Headers(fetchMock.mock.calls[0][1].headers);
      expect(sent.get("x-custom")).toBe("preserved");
      expect(sent.get("content-type")).toBe("application/json");
    },
  );

  it("keeps file uploads binary and exposes request IDs on upload errors", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "文件过大" }), {
      status: 413, headers: { "x-request-id": "upload-trace" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["正文"], "book.txt", { type: "text/plain" });
    const events: number[] = [];
    const receive = (event: Event) => events.push((event as CustomEvent<number>).detail);
    window.addEventListener("novel-writer:pending", receive);
    try {
      await expect(api("/api/imports/preview", { method: "POST", body: file }))
        .rejects.toMatchObject({ requestId: "upload-trace", status: 413 });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(fetchMock.mock.calls[0][1].body).toBe(file);
      expect(new Headers(fetchMock.mock.calls[0][1].headers).has("content-type")).toBe(false);
      expect(events).toEqual([1, 0]);
    } finally { window.removeEventListener("novel-writer:pending", receive); }
  });

  it("stops during read backoff without leaving a timer or issuing another request", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("offline"));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    const removeListener = vi.spyOn(controller.signal, "removeEventListener");
    const result = api("/health", { signal: controller.signal });
    const assertion = expect(result).rejects.toMatchObject({ name: "AbortError" });
    await vi.advanceTimersByTimeAsync(0);
    controller.abort();
    await assertion;
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(removeListener).toHaveBeenCalledWith("abort", expect.any(Function));
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps a write key if success headers arrive but its response body cannot be read", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response("truncated json", { headers: { "x-request-id": "partial" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ saved: true })));
    vi.stubGlobal("fetch", fetchMock);
    const operations = new StableWriteOperationKeys();
    const init = { method: "PUT", body: jsonBody({ base_version: 1 }) };
    await expect(operations.request("/api/projects/p/story-blueprint", init)).rejects.toBeInstanceOf(ApiResponseError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await operations.request("/api/projects/p/story-blueprint", init);
    const keys = fetchMock.mock.calls.map((call) => new Headers(call[1].headers).get("Idempotency-Key"));
    expect(keys[0]).toBe(keys[1]);
  });

  it("accepts an explicit empty response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    await expect(api<void>("/api/operation", { method: "POST" })).resolves.toBeUndefined();
  });
});
