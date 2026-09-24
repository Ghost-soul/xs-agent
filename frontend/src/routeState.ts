export type PersistedView =
  | "home" | "models" | "blueprint" | "reader" | "longform"
  | "style" | "data" | "versions" | "generation";

const views = new Set<PersistedView>([
  "home", "models", "blueprint", "reader", "longform", "style",
  "data", "versions", "generation",
]);

export type AppRouteState = {
  projectId: string | null;
  view: PersistedView;
  chapterId: string | null;
};

export function readRoute(location: Pick<Location, "pathname" | "search"> = window.location): AppRouteState {
  const query = new URLSearchParams(location.search);
  const projectRoute = matchPath("/projects/:projectId/:surface", location.pathname);
  const pathView = projectRoute ? viewFromPath(projectRoute.params.surface ?? "", query) : null;
  const legacyView = query.get("view");
  return {
    projectId: (projectRoute?.params.projectId ?? query.get("project")) || null,
    view: pathView ?? (legacyView && views.has(legacyView as PersistedView) ? legacyView as PersistedView : "home"),
    chapterId: query.get("chapter") || null,
  };
}

export function routeHref(
  next: Partial<AppRouteState>,
  location: Pick<Location, "pathname" | "search"> = window.location,
): string {
  const current = readRoute(location);
  const value = { ...current, ...next };
  const query = createSearchParams();
  if (value.chapterId) query.set("chapter", value.chapterId);
  const pathname = value.projectId ? `/projects/${value.projectId}/${pathForView(value.view, query)}` : "/";
  return query.toString() ? `${pathname}?${query}` : pathname;
}

function viewFromPath(segment: string, query: URLSearchParams): PersistedView | null {
  if (segment === "home") return "home";
  if (segment === "create") return "generation";
  if (segment === "write" || segment === "review") return "home";
  if (segment === "read") return "reader";
  if (segment === "story") return query.get("section") === "continuity" ? "longform" : "blueprint";
  if (segment === "data") return "data";
  if (segment !== "settings") return null;
  const tab = query.get("tab");
  return tab && views.has(tab as PersistedView) ? tab as PersistedView : "models";
}

function pathForView(view: PersistedView, query: URLSearchParams): string {
  if (view === "home") return "home";
  if (view === "generation") return "create";
  if (view === "reader") return "read";
  if (view === "blueprint") return "story";
  if (view === "longform") { query.set("section", "continuity"); return "story"; }
  if (view === "data") return "data";
  query.set("tab", view);
  return "settings";
}
import { createSearchParams, matchPath } from "react-router-dom";
