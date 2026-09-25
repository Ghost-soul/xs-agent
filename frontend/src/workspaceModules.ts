import { lazyWorkspace } from "./lazyWorkspace";

export const GenerationWorkspace = lazyWorkspace(async () => ({
  default: (await import("./GenerationWorkspace")).GenerationWorkspace,
}));

export const BlueprintWorkspace = lazyWorkspace(async () => ({
  default: (await import("./BlueprintWorkspace")).BlueprintWorkspace,
}));
export const DataWorkspace = lazyWorkspace(async () => ({
  default: (await import("./DataWorkspace")).DataWorkspace,
}));
export const LongformWorkspace = lazyWorkspace(async () => ({
  default: (await import("./LongformWorkspace")).LongformWorkspace,
}));
export const ModelWorkspace = lazyWorkspace(async () => ({
  default: (await import("./ModelWorkspace")).ModelWorkspace,
}));
export const PromptWorkspace = lazyWorkspace(async () => ({
  default: (await import("./PromptWorkspace")).PromptWorkspace,
}));
export const ReaderView = lazyWorkspace(async () => ({
  default: (await import("./ReaderView")).ReaderView,
}));
export const StyleWorkspace = lazyWorkspace(async () => ({
  default: (await import("./StyleWorkspace")).StyleWorkspace,
}));

const workspaces = {
  generation: GenerationWorkspace,
  blueprint: BlueprintWorkspace,
  data: DataWorkspace,
  longform: LongformWorkspace,
  models: ModelWorkspace,
  prompts: PromptWorkspace,
  reader: ReaderView,
  style: StyleWorkspace,
};

export function preloadWorkspace(view: string | undefined): void {
  if (view && Object.hasOwn(workspaces, view)) {
    void workspaces[view as keyof typeof workspaces].preload().catch(() => undefined);
  }
}
