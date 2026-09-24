import { useEffect, useId, useState } from "react";
import { api, errorMessage, isAbortError, type GenerationDetail } from "./api";
import { actionName, callStatusName, chronologicalCalls, revalidationBlocker } from "./GenerationProgress";
import { copyPlainText } from "./novelText";

type Call = GenerationDetail["calls"][number];
type Receipt = {
  status: string;
  request_sha256?: string;
  request: {
    model_request?: { model?: string; system_prompt?: string; user_prompt?: string; max_output_tokens?: number };
    wire_body?: string;
  };
  response: {
    text?: string;
    raw_response?: string;
    error_code?: string;
    terminal?: { finish_reason?: string; terminal_status?: string; terminal_event_seen?: boolean };
  } | null;
};
const parts = [
  { key: "overview", label: "输入与输出" },
  { key: "system", label: "系统 Prompt" },
  { key: "user", label: "任务 Prompt" },
  { key: "output", label: "模型原始输出" },
  { key: "wire", label: "实际请求体" },
  { key: "raw", label: "供应商原始响应" },
] as const;
type Part = typeof parts[number]["key"];
type SourcePart = Exclude<Part, "overview">;
const roles = ["Chief", "Writer", "Memory", "Checker", "Reader", "Editor"] as const;
type Role = typeof roles[number] | "其他";

function callRole(action: unknown): Role {
  const name = String(action);
  if (["plan", "review"].includes(name) || name.startsWith("chief:")) return "Chief";
  if (["write", "rewrite"].includes(name) || name.startsWith("write:")) return "Writer";
  if (/^memory(?:$|:|_)/u.test(name)) return "Memory";
  if (/^checker(?:$|_)/u.test(name)) return "Checker";
  if (/^reader(?:$|_)/u.test(name)) return "Reader";
  if (["editor", "title", "amend"].includes(name)) return "Editor";
  return "其他";
}

function download(content: string, filename: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type: `${type};charset=utf-8` }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function CallInspector({ base, batchId, call }: { base: string; batchId: string; call: Call }) {
  const id = useId();
  const [part, setPart] = useState<Part>("overview");
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const path = `${base}/${batchId}/calls/${String(call.id)}`;
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setReceipt(null);
    setError("");
    setNotice("");
    void api<Receipt>(path, { signal: controller.signal }).then((value) => {
      if (controller.signal.aborted) return;
      if (!value || !value.request || typeof value.request !== "object") throw new Error("调用记录格式无效，请刷新后重试。");
      setReceipt(value);
    }).catch((caught) => {
      if (!controller.signal.aborted && !isAbortError(caught)) setError(errorMessage(caught));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [path, call.status, call.finished_at, refresh]);

  const request = receipt?.request.model_request;
  const response = receipt?.response;
  const values: Record<SourcePart, string | undefined> = {
    system: request?.system_prompt, user: request?.user_prompt, output: response?.text,
    wire: receipt?.request.wire_body, raw: response?.raw_response,
  };
  const value = part === "overview" ? undefined : values[part];
  const available = typeof value === "string";
  const label = parts.find((p) => p.key === part)!.label;
  const terminal = response?.terminal;
  const incomplete = !!response && (
    response.error_code === "token_limit_exceeded"
    || ["length", "max_tokens", "max_output_tokens"].includes(terminal?.finish_reason ?? "")
    || ["failed", "incomplete"].includes(terminal?.terminal_status ?? "")
  );
  const filename = `${String(call.action).replace(/[^a-zA-Z0-9_-]/g, "-")}-${String(call.id)}`;
  function source(key: SourcePart) {
    const content = values[key];
    const title = parts.find((p) => p.key === key)!.label;
    return typeof content === "string" ? <>
      <p className="agent-call-note">{content.length.toLocaleString()} 字符 · 原文完整保留</p>
      {content.length ? <pre className="agent-call-source" aria-label={title}>{content}</pre> : <p>已保存的{title}为空。</p>}
    </> : <p>此记录未保存{title}。</p>;
  }
  function copy(content: string, title: string) {
    setNotice("");
    void copyPlainText(content).then(() => setNotice(`已复制${title}原文。`)).catch((caught) => setNotice(errorMessage(caught, "复制失败，可下载原文。")));
  }
  function overviewSource(key: "system" | "user" | "output") {
    const title = parts.find((p) => p.key === key)!.label;
    const content = values[key];
    return <section className="agent-prompt-block" aria-label={`${title}原文`}>
      <h5>{title}</h5>
      <div className="button-row">
        <button type="button" disabled={typeof content !== "string"} onClick={() => copy(content!, title)}>复制{title}</button>
        <button type="button" disabled={typeof content !== "string"} onClick={() => download(content!, `${filename}-${key}.txt`, "text/plain")}>下载{title}</button>
      </div>
      {source(key)}
    </section>;
  }
  return <section className="agent-call-inspector" aria-label={`${actionName(call.action)}的原始记录`} aria-busy={loading}>
    <div className="agent-call-heading"><h4>{actionName(call.action)} · 原始输入与输出</h4>
      <button type="button" disabled={loading} onClick={() => setRefresh((n) => n + 1)}>刷新记录</button>
    </div>
    <p className="agent-call-note">查看的是这一次调用保存的原文；复制与下载不会重新调用模型。</p>
    {loading && <p role="status">正在读取已保存的调用记录…</p>}
    {error && <p role="alert">{error}</p>}
    {receipt && <>
      <p className="agent-call-meta">模型：{request?.model || "未记录"} · {callStatusName(receipt.status)}{terminal?.finish_reason ? ` · 结束原因：${terminal.finish_reason}` : ""}</p>
      {incomplete && <p role="alert">该响应未完整结束。以下保留当时已收到的内容。</p>}
      {!response && <p role="status">尚未保存模型响应。可稍后刷新查看；没有响应记录不代表请求未执行。</p>}
      <div className="agent-source-tabs" role="tablist" aria-label="原始记录内容">
        {parts.map((p, index) => <button key={p.key} type="button" role="tab" id={`${id}-${p.key}`} aria-selected={part === p.key} aria-controls={`${id}-content`} tabIndex={part === p.key ? 0 : -1}
          onClick={() => { setPart(p.key); setNotice(""); }} onKeyDown={(event) => {
            const target = event.key === "ArrowRight" ? (index + 1) % parts.length
              : event.key === "ArrowLeft" ? (index + parts.length - 1) % parts.length
              : event.key === "Home" ? 0 : event.key === "End" ? parts.length - 1 : null;
            if (target === null) return;
            event.preventDefault();
            setPart(parts[target].key);
            setNotice("");
            document.getElementById(`${id}-${parts[target].key}`)?.focus();
          }}>{p.label}</button>)}
      </div>
      <div role="tabpanel" id={`${id}-content`} aria-labelledby={`${id}-${part}`} tabIndex={0}>
        {part === "overview" && <div className="agent-prompt-columns">
          <div><h4>输入 Prompt</h4><p className="agent-call-note">实际发送给该角色的系统要求、任务及故事上下文。</p>{overviewSource("system")}{overviewSource("user")}</div>
          <div><h4>模型输出</h4><p className="agent-call-note">该次模型返回的原文，包含解析失败或截断时保存的内容。</p>{overviewSource("output")}</div>
        </div>}
        {part === "user" && <p className="agent-call-note">包含本次实际送入的任务、故事资料和上下文。</p>}
        {part === "wire" && <p className="agent-call-note">保存的供应商协议请求体，可核对最终消息及模型参数。</p>}
        {part === "raw" && <p className="agent-call-note">供应商返回的原始响应，可能包含 JSON 或流式事件。</p>}
        <div className="button-row">
          {part !== "overview" && <><button type="button" disabled={!available} onClick={() => copy(value!, label)}>复制原文</button>
          <button type="button" disabled={!available} onClick={() => download(value!, `${filename}-${part}.txt`, "text/plain")}>下载原文</button></>}
          <button type="button" onClick={() => download(JSON.stringify({ batch_id: batchId, call_id: call.id, action: call.action, ...receipt }, null, 2), `${filename}.json`, "application/json")}>下载完整调用记录</button>
        </div>
        {notice && <p role="status">{notice}</p>}
        {part !== "overview" && source(part)}
      </div>
    </>}
  </section>;
}

export function GenerationCallLog({ base, batch, busy, onRevalidate }: {
  base: string; batch: GenerationDetail; busy: boolean; onRevalidate: (callId: string) => Promise<void>;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [role, setRole] = useState<Role | "全部">("全部");
  const calls = chronologicalCalls(batch);
  const visible = role === "全部" ? calls : calls.filter((call) => callRole(call.action) === role);
  const options: (Role | "全部")[] = ["全部", ...roles, ...(calls.some((call) => callRole(call.action) === "其他") ? ["其他" as const] : [])];
  return <details open className="agent-call-log"><summary>调用、费用与已保存响应（已用 {batch.calls.length}；主链上限 {Number(batch.snapshot.maximum_calls ?? 3)}{batch.state.memory_output_authorization_id ? "，另已授权一次 Memory 补全" : ""}）</summary>
    <h3>各角色 Prompt 与输出</h3>
    <p>选择角色查看最近一次输入与输出，也可展开该角色此前各单元的调用。</p>
    <div className="agent-role-picker" role="group" aria-label="按角色查看调用">
      {options.map((item) => {
        const matching = item === "全部" ? calls : calls.filter((call) => callRole(call.action) === item);
        return <button key={item} type="button" aria-pressed={role === item} onClick={() => {
          setRole(item); setSelected(item === "全部" ? null : String(matching.at(-1)?.id ?? "") || null);
        }}>{item}（{matching.length}）</button>;
      })}
    </div>
    {!visible.length && <p>{role === "全部" ? "本批次尚无调用记录。" : `${role} 在本批次尚无调用记录，执行后才会有实际输入与输出。`}</p>}
    {visible.map((call) => {
      const callId = String(call.id);
      const open = selected === callId;
      const blocker = revalidationBlocker(batch, call);
      const canAttempt = ["local_failure", "response_saved"].includes(String(call.status));
      return <article key={callId} className="agent-call-row">
        <p><strong>{actionName(call.action)}</strong> · {callStatusName(call.status)} · {String(call.started_at ?? "")} · 输入 {String(call.input_tokens ?? "未知")} · 费用 ¥{String(call.actual_cost_cny ?? "未知")}</p>
        <button type="button" aria-expanded={open} aria-controls={`agent-call-${callId}`} aria-label={`${actionName(call.action)}：${open ? "收起" : "查看"} Prompt 与输出`} onClick={() => setSelected(open ? null : callId)}>{open ? "收起 Prompt 与输出" : "查看 Prompt 与输出"}</button>
        {canAttempt && !blocker && <button type="button" disabled={busy} onClick={() => void onRevalidate(callId)}>纯本地重验</button>}
        {canAttempt && blocker && <p>{blocker}</p>}
        <div id={`agent-call-${callId}`}>{open && <CallInspector key={`${base}/${batch.id}/${callId}`} base={base} batchId={batch.id} call={call} />}</div>
      </article>;
    })}
    {batch.artifacts.some((a) => a.kind === "restored_archive") && <details><summary>恢复的历史调用与费用证据</summary><pre>{JSON.stringify(batch.artifacts.filter((a) => a.kind === "restored_archive"), null, 2)}</pre></details>}
  </details>;
}
