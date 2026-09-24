import { lazy, type ComponentType } from "react";

export function lazyWorkspace<Props>(
  loader: () => Promise<{ default: ComponentType<Props> }>,
) {
  let pending: ReturnType<typeof loader> | undefined;
  const load = () => {
    pending ??= loader().catch((error: unknown) => {
      pending = undefined;
      throw error;
    });
    return pending;
  };
  return Object.assign(lazy(load), { preload: load });
}
