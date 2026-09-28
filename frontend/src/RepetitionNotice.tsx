export type RepetitionObservation = {
  method: string;
  body_sha256: string;
  minimum_paragraph_characters: number;
  characters: number;
  repeated_characters: number;
  repeated_paragraphs: number;
  percent: number;
  groups: number;
  examples: { first_paragraph: number; repeat_paragraph: number; occurrences: number; paragraph_characters: number }[];
};

export function RepetitionNotice({ value, scope }: { value?: RepetitionObservation | null; scope: string }) {
  if (!value?.repeated_characters) return null;
  return <details aria-label={`${scope}重复提示`}>
    <summary>{scope}：检测到 {value.repeated_characters.toLocaleString()} 字的重复段落，占 {value.percent}%</summary>
    <p>按换行分段、忽略空白及段尾结束标记，仅统计不少于 {value.minimum_paragraph_characters} 字的完全相同段落，首次出现不计入重复。未检测近似改写；字数达标不代表情节已充分推进。</p>
    {value.examples.length > 0 && <ul>{value.examples.map((example) => <li key={example.first_paragraph}>
      第 {example.first_paragraph} 段与第 {example.repeat_paragraph} 段相同，该段共出现 {example.occurrences} 次，每次 {example.paragraph_characters.toLocaleString()} 字。
    </li>)}</ul>}
    <p>仅供阅读核对，也可能是有意复现。原文完整保留，不影响继续或采用，不会自动删除、补写或重试。</p>
  </details>;
}
