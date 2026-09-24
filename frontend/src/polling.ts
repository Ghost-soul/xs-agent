type PollingOptions = {
  intervalMs: number;
  poll: (signal: AbortSignal) => Promise<void>;
  onError?: (error: unknown) => void;
  immediate?: boolean;
  timeoutMs?: number;
  runWhenHidden?: boolean;
};

/** Visible by default; each request settles before the next one is scheduled. */
export function startPolling({ poll, intervalMs, onError, immediate = true, timeoutMs = 15_000, runWhenHidden = false }: PollingOptions): () => void {
  let active = true;
  let running = false;
  let timer: number | undefined;
  let controller: AbortController | undefined;

  const schedule = () => {
    if (active && (runWhenHidden || !document.hidden)) timer = window.setTimeout(run, intervalMs);
  };
  const run = async () => {
    if (!active || running || (document.hidden && !runWhenHidden)) return;
    window.clearTimeout(timer);
    running = true;
    const request = new AbortController();
    controller = request;
    let timedOut = false;
    const timeout = window.setTimeout(() => { timedOut = true; request.abort(); }, timeoutMs);
    try {
      await poll(request.signal);
    } catch (error) {
      if (active && (timedOut || !request.signal.aborted)) onError?.(error);
    } finally {
      window.clearTimeout(timeout);
      running = false;
      controller = undefined;
      schedule();
    }
  };
  const refresh = () => { void run(); };
  const visibility = () => {
    if (runWhenHidden) return;
    window.clearTimeout(timer);
    if (document.hidden) controller?.abort();
    else refresh();
  };
  window.addEventListener("online", refresh);
  window.addEventListener("focus", refresh);
  document.addEventListener("visibilitychange", visibility);
  if (immediate) refresh();
  else schedule();
  return () => {
    active = false;
    window.clearTimeout(timer);
    controller?.abort();
    window.removeEventListener("online", refresh);
    window.removeEventListener("focus", refresh);
    document.removeEventListener("visibilitychange", visibility);
  };
}
