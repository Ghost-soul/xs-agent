import { cleanup, render, screen } from "@testing-library/react";
import { Suspense } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { lazyWorkspace } from "./lazyWorkspace";

afterEach(cleanup);

describe("intent-based workspace code loading", () => {
  it("shares one import across preload and render without mounting during preload", async () => {
    const mounted = vi.fn();
    const Page = ({ title }: { title: string }) => {
      mounted();
      return <h2>{title}</h2>;
    };
    const loader = vi.fn(async () => ({ default: Page }));
    const Workspace = lazyWorkspace(loader);

    expect(loader).not.toHaveBeenCalled();
    const first = Workspace.preload();
    expect(Workspace.preload()).toBe(first);
    await first;
    expect(mounted).not.toHaveBeenCalled();
    render(<Suspense fallback={<p>载入中</p>}><Workspace title="目标页面" /></Suspense>);

    expect(await screen.findByRole("heading", { name: "目标页面" })).toBeVisible();
    expect(loader).toHaveBeenCalledOnce();
  });

  it("does not make a failed speculative import poison the subsequent navigation", async () => {
    const Page = () => <h2>恢复后的页面</h2>;
    const loader = vi.fn<() => Promise<{ default: typeof Page }>>()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({ default: Page });
    const Workspace = lazyWorkspace(loader);

    await expect(Workspace.preload()).rejects.toThrow("offline");
    expect(loader).toHaveBeenCalledOnce();
    render(<Suspense fallback={<p>载入中</p>}><Workspace /></Suspense>);

    expect(await screen.findByRole("heading", { name: "恢复后的页面" })).toBeVisible();
    expect(loader).toHaveBeenCalledTimes(2);
  });
});
