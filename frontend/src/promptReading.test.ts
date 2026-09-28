import { describe, expect, it } from "vitest";
import { readPrompt } from "./promptReading";

describe("faithful prompt reading", () => {
  it("covers nested creative constraints, schema, empty values and unknown fields", () => {
    const result = readPrompt(JSON.stringify({
      creative_autonomy: { maximum_new_characters: 3, proposals_are_facts: false, new_character_slots: ["fixed-id"] },
      output_schema: { type: "object", properties: { new_characters: { type: "array", maxItems: 3, items: { type: "string" } } }, required: ["new_characters"] },
      new_characters: [], creative_notes: null, future_unknown: { deeply_nested: "完整保留" },
    }));
    expect(result.text).toContain('"新增人物上限（maximum_new_characters）": 3');
    expect(result.text).toContain('"候选设计是否已是事实（proposals_are_facts）": false');
    expect(result.text).toContain("列表项结构（items）");
    expect(result.text).toContain("fixed-id");
    expect(result.text).toContain('"新增人物设计（new_characters）": []');
    expect(result.text).toContain('"创作待定事项（无需答复）（creative_notes）": null');
    expect(result.text).toContain('"deeply_nested": "完整保留"');
    expect(result.sections).toBe(1);
  });

  it("reads all JSON blocks in custom templates without replacing surrounding instructions", () => {
    const source = '作者原话\r\n{"story_task":"Keep this English text."}\n\n附加规则\n```json\n{"creative_autonomy":{"maximum_new_characters":3}}\n```\n末尾要求';
    const result = readPrompt(source);
    expect(result.sections).toBe(2);
    expect(result.text).toContain("作者原话\r\n");
    expect(result.text).toContain('"作者任务（story_task）": "Keep this English text."');
    expect(result.text).toContain("附加规则\n```json\n");
    expect(result.text).toContain("新增人物上限");
    expect(result.text.endsWith("```\n末尾要求")).toBe(true);
  });

  it("keeps large numbers, duplicate fields, escapes and markup as supplied", () => {
    const result = readPrompt('{"id":900719925474099312345,"score":1.234567890123456789e-20,"name":"a","name":"b","text":"<script>x</script>\\n\\\"{}\\\"","__proto__":"own"}');
    expect(result.text).toContain("900719925474099312345");
    expect(result.text).toContain("1.234567890123456789e-20");
    expect(result.text.match(/名称（name）/gu)).toHaveLength(2);
    expect(result.text).toContain('"<script>x</script>\\n\\\"{}\\\""');
    expect(result.text).toContain('"__proto__": "own"');
  });

  it("preserves incomplete, non-JSON and deeply nested input rather than guessing or truncating", () => {
    for (const source of ['正文 {不是 JSON}\n原样', '{"name":"未闭合', '[这是普通说明]\n末段', "[".repeat(140) + "0" + "]".repeat(140)]) {
      expect(readPrompt(source).text).toBe(source);
    }
  });

  it("keeps the complete beginning and ending of long structured inputs", () => {
    const body = "很长的上下文。".repeat(30000) + "末尾证据";
    expect(readPrompt(JSON.stringify({ body, maximum_new_characters: 3 })).text).toContain(body);
  });
});
