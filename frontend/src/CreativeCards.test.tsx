import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it } from "vitest";
import { CreativeCardPool } from "./CreativeCards";

afterEach(cleanup);

it("keeps selected narratives when searching and replaces only the optional genre", () => {
  const cards = [
    { id: "world", name: "奇幻", layer: "genre" },
    { id: "side", name: "仙侠", layer: "genre" },
    { id: "gl", name: "百合", layer: "narrative" },
    { id: "farm", name: "种田文", layer: "narrative" },
    { id: "mystery", name: "推理", layer: "narrative" },
  ];
  function Harness() {
    const [selected, update] = useState(["gl"]);
    return <><CreativeCardPool cards={cards} primaryId="world" selected={selected} update={update} /><output>{JSON.stringify(selected)}</output></>;
  }
  render(<Harness />);
  fireEvent.change(screen.getByLabelText("副题材（可选）"), { target: { value: "side" } });
  fireEvent.click(screen.getByLabelText("叙事卡：种田文"));
  fireEvent.change(screen.getByLabelText("查找叙事卡"), { target: { value: "推理" } });
  expect(screen.getAllByRole("checkbox")).toHaveLength(1);
  fireEvent.click(screen.getByLabelText("叙事卡：推理"));
  expect(screen.getByRole("status")).toHaveTextContent('["side","gl","farm","mystery"]');
  fireEvent.change(screen.getByLabelText("副题材（可选）"), { target: { value: "" } });
  expect(screen.getByRole("status")).toHaveTextContent('["gl","farm","mystery"]');
});
