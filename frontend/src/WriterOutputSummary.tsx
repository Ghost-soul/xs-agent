import { RepetitionNotice, type RepetitionObservation } from "./RepetitionNotice";

export type WriterObservation = {
  call_id: string;
  characters: number;
  saved_characters: number;
  target: { min_characters: number; max_characters: number } | null;
  status: "below" | "within" | "above" | "not_requested";
  percent_of_minimum: number | null;
  terminal_marker: string | null;
  reasoning_effort: string | null;
  reasoning_tokens: number | null;
  call_status: string;
  ordinal?: number;
  complete?: boolean;
  repetition?: RepetitionObservation | null;
};

export function WriterOutputSummary({ value, reasoning = false }: { value?: WriterObservation | null; reasoning?: boolean }) {
  if (!value) return null;
  return <div aria-label={value.ordinal ? `第 ${value.ordinal} 单元篇幅` : "本次 Writer 输出"}>
    <p>{value.ordinal ? `第 ${value.ordinal} 单元：` : "本次输出："}{value.characters.toLocaleString()} 字
      {value.target && <>，该次请求目标 {value.target.min_characters.toLocaleString()}–{value.target.max_characters.toLocaleString()} 字；
        {value.status === "below" ? `${(value.percent_of_minimum ?? 100) < 50 ? "明显偏短" : "低于目标"}，达到目标下界的 ${value.percent_of_minimum}%` : value.status === "above" ? "超过目标上界" : "处于目标范围"}</>}
      {value.complete === false || value.call_status !== "completed" ? "。调用尚未完成，收到的片段保留。" : "。调用已完成。"}
      {value.terminal_marker && <>按排除末尾 {value.terminal_marker} 计数，保存原文为 {value.saved_characters.toLocaleString()} 字。</>}
    </p>
    {value.status === "below" && <p>篇幅尚未达到该次请求目标；可先阅读全文，再决定是否调整未写计划或单独修订正文。不会自动补写。</p>}
    <RepetitionNotice value={value.repetition} scope={value.ordinal ? `第 ${value.ordinal} 单元内` : "本次输出内"} />
    {reasoning && <p>推理：{(value.reasoning_tokens ?? 0) > 0
      ? `供应商报告 ${value.reasoning_tokens!.toLocaleString()} 个推理 tokens${value.reasoning_effort === "none" ? "，与关闭推理的请求不一致" : ""}`
      : value.reasoning_effort === "none" ? "请求已传关闭参数；实际执行以供应商用量为准"
      : value.reasoning_effort ? `请求参数为 ${value.reasoning_effort}` : "本次请求未传推理开关，不能确认已关闭"}。</p>}
  </div>;
}
