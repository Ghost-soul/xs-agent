export type DirtySurface = {
  key: string;
  label: string;
  save?: () => Promise<void>;
  discard?: () => void | Promise<void>;
  focus?: () => void;
};

const surfaces = new Map<string, DirtySurface>();

export function setDirtySurface(
  key: string,
  label: string,
  dirty: boolean,
  actions: Pick<DirtySurface, "save" | "discard" | "focus"> = {},
): void {
  if (dirty) surfaces.set(key, { key, label, ...actions });
  else surfaces.delete(key);
  window.dispatchEvent(new CustomEvent("novel-writer:dirty", { detail: getDirtySurfaces() }));
}

export async function saveDirtySurfaces(): Promise<void> {
  for (const surface of getDirtySurfaces()) {
    if (!surface.save) throw new Error(`“${surface.label}”没有可用的保存动作`);
    await surface.save();
  }
}

export async function discardDirtySurfaces(): Promise<void> {
  for (const surface of getDirtySurfaces()) await surface.discard?.();
}

export function getDirtySurfaces(): DirtySurface[] {
  return [...surfaces.values()];
}

export function clearDirtySurface(key: string): void {
  setDirtySurface(key, "", false);
}

export function installBeforeUnloadGuard(): () => void {
  const handler = (event: BeforeUnloadEvent) => {
    if (surfaces.size === 0) return;
    event.preventDefault();
    event.returnValue = "";
  };
  window.addEventListener("beforeunload", handler);
  return () => window.removeEventListener("beforeunload", handler);
}
