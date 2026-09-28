import { useMemo, useState } from "react";
import { readPrompt } from "./promptReading";

export function PromptContent({ text, label }: { text: string; label: string }) {
  const [original, setOriginal] = useState(false);
  const reading = useMemo(() => readPrompt(text), [text]);
  return <div className="prompt-content">
    <div className="prompt-reading-controls" role="group" aria-label={`${label}阅读方式`}>
      <button type="button" aria-pressed={!original} onClick={() => setOriginal(false)}>中文解读</button>
      <button type="button" aria-pressed={original} onClick={() => setOriginal(true)}>原文对照</button>
    </div>
    <p className="agent-call-note">{original ? "完整原文，含原始字段、空白与顺序。" : "完整展示当前输入，将已知结构字段加注中文；字段名、数值、ID 与正文保留。未知字段及英文自由文本原样展示，不猜译。true / false 表示是 / 否，null 表示空值。"}</p>
    <pre className="agent-call-source" aria-label={label}>{original ? text : reading.text}</pre>
  </div>;
}

export type InputBindings = {
  template_revision?: string | null;
  creative_autonomy?: { revision?: string } | null;
  role_output?: { revision?: string } | null;
  editable_rules?: { revision?: string } | null;
};

export function PromptSourceBindings({ value, preview = false }: { value?: InputBindings; preview?: boolean }) {
  if (!value) return null;
  return <details className="prompt-source-bindings"><summary>{preview ? "所选资料的原模板与规则版本" : "此份输入的模板与规则版本"}</summary>
    {preview && <p>预览使用上方当前草稿；下列是资料来源当时绑定的版本。</p>}
    <p>{preview ? "来源原模板" : "模板"}：{value.template_revision ?? "内置文本或该记录未保存模板版本"}</p>
    <p>创作自主规则：{value.creative_autonomy?.revision ?? "此来源未绑定新增人物规则，不套用当前默认"}</p>
    <p>输出规则：{value.role_output?.revision ?? "历史规则或未记录"}</p>
    {value.editable_rules && <p>附加指导版本：{value.editable_rules.revision}</p>}
  </details>;
}
