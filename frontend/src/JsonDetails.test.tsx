import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { JsonDetails } from "./JsonDetails";

afterEach(cleanup);

it("leaves large audit data unformatted until opened and reuses unchanged open content", () => {
  const toJSON = vi.fn(() => ({ evidence: "完整原始资料" }));
  const value = { toJSON };
  const view = render(<JsonDetails title="完整资料" value={value} />);
  expect(toJSON).not.toHaveBeenCalled();
  const details = screen.getByText("完整资料").closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  expect(screen.getByText(/完整原始资料/)).toBeVisible();
  expect(toJSON).toHaveBeenCalledOnce();
  view.rerender(<JsonDetails title="完整资料" value={value} />);
  expect(toJSON).toHaveBeenCalledOnce();
});
