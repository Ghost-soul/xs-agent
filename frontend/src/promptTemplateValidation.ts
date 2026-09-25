type Template = { system_text: string; task_template: string };
type Field = { key: string; label: string; required: boolean };
export type TemplateIssue = { message: string; start?: number; end?: number; key?: string };

export function templateTokens(text: string) {
  return [...text.matchAll(/\{\{\s*([a-z][a-z0-9_.]*)\s*\}\}/g)].map((match) => ({
    key: match[1], start: match.index, end: match.index + match[0].length,
  }));
}

export function templateIssues(template: Template, fields: Field[]): TemplateIssue[] {
  const issues: TemplateIssue[] = [];
  if (!template.system_text.trim()) issues.push({ message: "系统 Prompt 默认文本不能为空。" });
  if (!template.task_template.trim()) issues.push({ message: "任务 Prompt 模板不能为空。" });
  const tokens = templateTokens(template.task_template);
  const byKey = new Map<string, typeof tokens>();
  for (const token of tokens) byKey.set(token.key, [...(byKey.get(token.key) ?? []), token]);
  const labels = new Map(fields.map((field) => [field.key, field.label]));
  for (const [key, occurrences] of byKey) {
    if (!labels.has(key)) issues.push({
      message: `未知占位符 {{${key}}}，请使用本角色的可用字段。`, ...occurrences[0],
    });
    if (occurrences.length > 1) issues.push({
      message: `{{${key}}}（${labels.get(key) ?? key}）重复出现 ${occurrences.length} 次，请只保留一处。`,
      ...occurrences[1],
    });
  }
  for (const field of fields) {
    if (field.required && !byKey.has(field.key)) issues.push({
      message: `缺少必要资料：${field.label} {{${field.key}}}。`, key: field.key,
    });
  }
  const residue = template.task_template.replace(/\{\{\s*([a-z][a-z0-9_.]*)\s*\}\}/g, "");
  if (residue.includes("{{") || residue.includes("}}")) issues.push({
    message: "占位符格式有误，请使用 {{字段名}}，不支持表达式。",
  });
  return issues;
}
