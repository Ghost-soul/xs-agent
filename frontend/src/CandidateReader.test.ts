import { describe, expect, it } from "vitest";
import { candidateSections } from "./CandidateReader";

describe("candidate reading boundaries", () => {
  it("reads Python offsets without splitting astral characters or losing separators in full text", () => {
    const first = "𠮷安推开门。\n\n院子里亮着灯。";
    const last = "她把信放在桌上。🌙";
    const body = `${first}\n\n${last}`;
    const end = Array.from(first).length;
    expect(candidateSections(body, [
      { ordinal: 1, start: 0, end, complete: true },
      { ordinal: 2, start: end + 2, end: Array.from(body).length, complete: false },
    ])).toEqual([
      { label: "叙事单元 1", body: first },
      { label: "叙事单元 2（未完成）", body: last },
    ]);
  });

  it.each([
    undefined,
    [],
    [{ ordinal: 1, start: 0, end: 1 }],
    [{ ordinal: 1, start: 0, end: 8 }],
    [{ ordinal: 1, start: 1, end: 6 }],
    [{ ordinal: 1, start: 0, end: 3 }, { ordinal: 2, start: 2, end: 6 }],
    [{ ordinal: 1, start: 0, end: 2 }, { ordinal: 2, start: 3, end: 6 }],
    [{ ordinal: 2, start: 0, end: 6 }],
  ])("falls back to full text when unit boundaries are missing or do not cover the current body", (units) => {
    expect(candidateSections("窗外天色渐明", units)).toEqual([]);
  });
});
