export type * from "./apiTypes";

const API_ROOT = "/backend";
let pendingRequests = 0;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly requestId: string | null,
    readonly detail: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export class ApiNetworkError extends Error {
  constructor(
    readonly method: string,
    readonly path: string,
    readonly safeToRetry: boolean,
    options?: { cause?: unknown },
  ) {
    super(
      safeToRetry
        ? "暂时无法连接应用服务；只读请求已停止，恢复连接后可安全刷新。"
        : "与应用服务的连接在写入请求期间中断；系统不会自动重发，以避免重复调用或重复费用。",
      options,
    );
    this.name = "ApiNetworkError";
  }
}

function reportPendingRequests(delta: number) {
  pendingRequests = Math.max(0, pendingRequests + delta);
  window.dispatchEvent(new CustomEvent("novel-writer:pending", { detail: pendingRequests }));
}

export class ApiResponseError extends Error {
  constructor(readonly status: number, readonly requestId: string | null, cause: unknown) {
    super(`应用服务返回了无法读取的响应；请刷新核对操作结果${requestId ? `（请求 ID: ${requestId}）` : ""}`, { cause });
    this.name = "ApiResponseError";
  }
}

export function isAbortError(value: unknown): boolean {
  return value instanceof Error && value.name === "AbortError";
}

function retryDelay(milliseconds: number, signal?: AbortSignal | null): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => {
      window.clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      reject(new DOMException("The operation was aborted", "AbortError"));
    };
    const timer = window.setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) abort();
  });
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const safeToRetry = ["GET", "HEAD", "OPTIONS"].includes(method);
  const trackAsOperation = !safeToRetry;
  if (trackAsOperation) reportPendingRequests(1);
  try {
    const headers = new Headers(init.headers);
    if (!headers.has("Accept")) headers.set("Accept", "application/json");
    // jsonBody returns a string. File/FormData keep their browser-selected media type.
    if (typeof init.body === "string" && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    let response: Response | null = null;
    let networkFailure: unknown = null;
    const attempts = safeToRetry ? 3 : 1;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      init.signal?.throwIfAborted();
      try {
        response = await fetch(API_ROOT + path, {
          ...init,
          headers,
        });
        break;
      } catch (caught) {
        if (isAbortError(caught) || init.signal?.aborted) throw caught;
        networkFailure = caught;
        if (attempt + 1 < attempts) await retryDelay(250 * (attempt + 1), init.signal);
      }
    }
    if (!response) throw new ApiNetworkError(method, path, safeToRetry, { cause: networkFailure });
    if (!response.ok) {
      let message = "请求失败（" + response.status + "）";
      let detail: unknown = null;
      try {
        const payload = (await response.json()) as { detail?: unknown };
        detail = payload.detail;
        if (detail !== undefined) message = formatApiErrorDetail(detail);
      } catch {
        // Keep the status-based fallback.
      }
      const requestId = response.headers.get("x-request-id");
      if (requestId) message += `（请求 ID: ${requestId}）`;
      throw new ApiError(message, response.status, requestId, detail);
    }
    if (response.status === 204 || method === "HEAD") return undefined as T;
    try {
      return (await response.json()) as T;
    } catch (caught) {
      if (isAbortError(caught)) throw caught;
      throw new ApiResponseError(response.status, response.headers.get("x-request-id"), caught);
    }
  } finally {
    if (trackAsOperation) reportPendingRequests(-1);
  }
}

export function errorMessage(value: unknown, fallback = "操作失败"): string {
  if (value instanceof Error && value.message.trim()) return value.message;
  return formatApiErrorDetail(value, fallback);
}

function formatApiErrorDetail(value: unknown, fallback = "请求失败"): string {
  if (typeof value === "string" && value.trim()) return value;
  if (Array.isArray(value)) {
    const messages = value.map((item) => formatApiErrorDetail(item, "")).filter(Boolean);
    return messages.length ? messages.join("；") : fallback;
  }
  if (value && typeof value === "object") {
    const detail = value as Record<string, unknown>;
    const message = detail.message ?? detail.msg ?? detail.reason ?? detail.error;
    const location = Array.isArray(detail.loc)
      ? detail.loc.filter((item) => item !== "body").map(String).join(" → ")
      : "";
    if (message !== undefined) {
      const readable = formatApiErrorDetail(message, fallback);
      return location ? `${location}：${readable}` : readable;
    }
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return fallback;
    }
  }
  return value == null || value === "" ? fallback : String(value);
}

export function commandHeaders(): HeadersInit {
  return { "Idempotency-Key": crypto.randomUUID() };
}

export class StableWriteOperationKeys {
  private readonly keys = new Map<string, string>();

  async request<T>(path: string, init: RequestInit): Promise<T> {
    const method = (init.method ?? "GET").toUpperCase();
    if (["GET", "HEAD", "OPTIONS"].includes(method)) {
      throw new Error("stable operation keys are only available for write requests");
    }
    const body = typeof init.body === "string" ? init.body : "";
    const signature = JSON.stringify([method, path, body]);
    const key = this.keys.get(signature) ?? crypto.randomUUID();
    this.keys.set(signature, key);
    const headers: Record<string, string> = {};
    new Headers(init.headers).forEach((value, name) => {
      headers[name] = value;
    });
    headers["Idempotency-Key"] = key;
    try {
      const response = await api<T>(path, { ...init, headers });
      this.keys.delete(signature);
      return response;
    } catch (caught) {
      if (!(caught instanceof ApiNetworkError || caught instanceof ApiResponseError) && !isAbortError(caught)) {
        this.keys.delete(signature);
      }
      throw caught;
    }
  }

  clear(): void {
    this.keys.clear();
  }
}

export function jsonBody(value: unknown): string {
  return JSON.stringify(value);
}
