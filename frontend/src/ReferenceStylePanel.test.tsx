import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReferenceStylePanel } from "./ReferenceStylePanel";

afterEach(() => { vi.unstubAllGlobals(); });

describe("ReferenceStylePanel", () => {
  it("separates local analysis consent from Provider excerpt consent", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        expect(body.local_analysis_allowed).toBe(true);
        expect(body.provider_excerpt_allowed).toBe(false);
      }
      return {
        ok: true,
        json: async () => ({ manifest: null, samples: [], profiles: [] }),
      } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ReferenceStylePanel projectId="project-1" />);

    const register = await screen.findByRole("button", { name: "登记并生成候选" });
    const sourcePath = screen.getByLabelText("本地源文件");
    const localConsent = screen.getByLabelText(/只在本机读取、清洗和分析/);
    const providerConsent = screen.getByLabelText(/另行允许未来把我选中的短样本发给 Provider/);
    expect(register).toBeDisabled();
    expect(providerConsent).not.toBeChecked();
    expect(sourcePath).toHaveValue("");
    expect(screen.getByRole("heading", { name: "参考文风档案" })).toBeInTheDocument();
    expect(screen.queryByText(/内部作品|参考原作/)).not.toBeInTheDocument();

    fireEvent.change(sourcePath, { target: { value: "F:\\novels\\reference.txt" } });
    fireEvent.click(localConsent);
    expect(register).toBeEnabled();
    fireEvent.click(register);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/backend/api/projects/project-1/reference-style/corpora",
      expect.objectContaining({ method: "POST" }),
    ));
  });
});
