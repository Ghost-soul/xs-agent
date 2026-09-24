import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type SyntheticEvent,
} from "react";
import { createBrowserRouter, RouterProvider, useBlocker, useLocation, useNavigate } from "react-router-dom";

import { FormalTitleEditor } from "./workflow/FormalTitleEditor";
import { VersionHistory } from "./workflow/VersionHistory";

export { FormalTitleEditor, VersionHistory };
import { GlobalRequestStatus } from "./GlobalRequestStatus";
import { GlobalSearch } from "./GlobalSearch";
import { ProjectDialog } from "./AppDialogs";
import { ProjectDeleteButton, ProjectDeletionHistory, type DeletionReceipt } from "./ProjectDeletion";
import { deleteProjectBrowserData } from "./localDrafts";
import { ConfirmationHost, requestConfirmation } from "./confirmation";
import {
  api,
  commandHeaders,
  jsonBody,
  type ArchivedProjectSummary,
  type ChapterWorkflow,
  type ProjectSummary,
  type ProjectSetupPayload,
  type ServiceHealth,
  type StoryRestartPreview,
  type VersionSummary,
  type VersionPrunePreview,
} from "./api";
import { publishProjectInvalidation } from "./projectOperations";
import { readRoute, routeHref, type PersistedView } from "./routeState";
import { startPolling } from "./polling";
import {
  discardDirtySurfaces,
  getDirtySurfaces,
  installBeforeUnloadGuard,
  saveDirtySurfaces,
  type DirtySurface,
} from "./unsavedChanges";

import {
  BlueprintWorkspace, DataWorkspace, GenerationWorkspace, LongformWorkspace, ModelWorkspace,
  ReaderView, StyleWorkspace,
  preloadWorkspace,
} from "./workspaceModules";

type ServiceState = "checking" | "ready" | "unavailable";
type ViewMode = PersistedView;
type DialogState = "project" | null;
type DangerDialogState = {
  title: string;
  summary: string;
  confirmLabel: string;
  confirm: () => Promise<void>;
} | null;

export function App() {
  const [router] = useState(() => createBrowserRouter([{ path: "*", element: <AppContent /> }]));
  return <RouterProvider router={router} />;
}

function AppContent() {
  const location = useLocation();
  const navigate = useNavigate();
  const blocker = useBlocker(() => getDirtySurfaces().length > 0);
  const initialRoute = readRoute(location);
  const [serviceState, setServiceState] = useState<ServiceState>("checking");
  const [routeInitialized, setRouteInitialized] = useState(false);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [archivedProjects, setArchivedProjects] = useState<ArchivedProjectSummary[]>([]);
  const [showArchivedProjects, setShowArchivedProjects] = useState(false);
  const [latestDeletion, setLatestDeletion] = useState<DeletionReceipt | null>(null);
  const deletedProjectIdsRef = useRef(new Set<string>());
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [chapterPaneOpen, setChapterPaneOpen] = useState(false);
  const directoryToggleRef = useRef<HTMLButtonElement>(null);
  const directoryCloseRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (chapterPaneOpen) directoryCloseRef.current?.focus();
  }, [chapterPaneOpen]);
  const closeChapterPane = () => {
    setChapterPaneOpen(false);
    directoryToggleRef.current?.focus();
  };
  const [projectQuery, setProjectQuery] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(initialRoute.projectId);
  const [chapters, setChapters] = useState<ChapterWorkflow[]>([]);
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(initialRoute.chapterId);
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [viewMode, setViewModeState] = useState<ViewMode>(initialRoute.view as ViewMode);
  const [advancedMode, setAdvancedMode] = useState(() => initialRoute.view === "models" || initialRoute.view === "style" || initialRoute.view === "versions");
  const [pendingNavigation, setPendingNavigation] = useState<(() => void) | null>(null);
  const [dirtySurfaces, setDirtySurfaces] = useState<DirtySurface[]>(() => getDirtySurfaces());
  const [dialog, setDialog] = useState<DialogState>(null);
  const [dangerDialog, setDangerDialog] = useState<DangerDialogState>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editingTitles, setEditingTitles] = useState(false);
  const selectedProjectIdRef = useRef<string | null>(initialRoute.projectId);
  const selectedChapterIdRef = useRef<string | null>(initialRoute.chapterId);
  const projectLoadRevisionRef = useRef(0);
  const skipInitializedProjectLoadRef = useRef<string | null>(null);

  const navigateTo = useCallback((href: string, replace = false) => {
    navigate(href, { replace });
  }, [navigate]);

  const navigateSearchResult = useCallback((href: string) => {
    const navigate = () => { void navigateTo(href); };
    if (getDirtySurfaces().length > 0) setPendingNavigation(() => navigate);
    else navigate();
  }, [navigateTo]);

  const setViewMode = useCallback((next: ViewMode) => {
    const navigate = () => {
      preloadWorkspace(next);
      setViewModeState(next);
      void navigateTo(routeHref({ projectId: selectedProjectId, view: next }, location));
    };
    if (getDirtySurfaces().length > 0) setPendingNavigation(() => navigate);
    else navigate();
  }, [location, navigateTo, selectedProjectId]);

  const selectProject = useCallback((projectId: string) => {
    const navigate = () => {
      setSelectedProjectId(projectId);
      setSelectedChapterId(null);
      void navigateTo(routeHref({ projectId, chapterId: null }, location));
    };
    if (getDirtySurfaces().length > 0) setPendingNavigation(() => navigate);
    else navigate();
  }, [location, navigateTo]);

  useEffect(() => {
    const dirty = () => setDirtySurfaces(getDirtySurfaces());
    window.addEventListener("novel-writer:dirty", dirty);
    const removeUnloadGuard = installBeforeUnloadGuard();
    return () => {
      window.removeEventListener("novel-writer:dirty", dirty);
      removeUnloadGuard();
    };
  }, []);

  useEffect(() => {
    const route = readRoute(location);
    setViewModeState(route.view as ViewMode);
    setSelectedProjectId(route.projectId);
    setSelectedChapterId(route.chapterId);
  }, [location]);

  useEffect(() => {
    if (blocker.state !== "blocked") return;
    setPendingNavigation(() => () => blocker.proceed());
  }, [blocker]);

  useEffect(() => {
    const shortcuts = (event: KeyboardEvent) => {
      if (event.isComposing) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        document.getElementById("global-search-input")?.focus();
      } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        if (getDirtySurfaces().length === 0) return;
        event.preventDefault();
        setBusy(true);
        void saveDirtySurfaces().catch((caught: unknown) => setError(errorMessage(caught, "保存失败"))).finally(() => setBusy(false));
      } else if (event.key === "Escape") {
        if (dangerDialog) setDangerDialog(null);
        else if (pendingNavigation) {
          if (blocker.state === "blocked") blocker.reset();
          setPendingNavigation(null);
        }
        else if (dialog) setDialog(null);
      }
    };
    window.addEventListener("keydown", shortcuts);
    return () => window.removeEventListener("keydown", shortcuts);
  }, [blocker, dangerDialog, dialog, pendingNavigation]);

  useEffect(() => {
    if (!routeInitialized) return;
    if (location.pathname === "/" && !location.search) return;
    const href = routeHref({ projectId: selectedProjectId, view: viewMode, chapterId: selectedChapterId }, location);
    if (href !== `${location.pathname}${location.search}`) navigateTo(href, true);
  }, [location, navigateTo, routeInitialized, selectedChapterId, selectedProjectId, viewMode]);

  const selectedProject = useMemo(
    () => projects.find((item) => item.project_id === selectedProjectId) ?? null,
    [projects, selectedProjectId],
  );
  const visibleProjects = useMemo(
    () => projects.filter((item) => item.title.toLocaleLowerCase().includes(projectQuery.trim().toLocaleLowerCase())),
    [projectQuery, projects],
  );
  useEffect(() => { selectedProjectIdRef.current = selectedProjectId; }, [selectedProjectId]);
  useEffect(() => { selectedChapterIdRef.current = selectedChapterId; }, [selectedChapterId]);
  const loadProjects = useCallback(async (preferredId?: string, signal?: AbortSignal) => {
    const [loadedItems, loadedArchivedItems] = await Promise.all([
      api<ProjectSummary[]>("/api/projects", { signal }),
      api<ArchivedProjectSummary[]>("/api/projects/archived", { signal }),
    ]);
    if (signal?.aborted) return;
    const items = loadedItems.filter((item) => !deletedProjectIdsRef.current.has(item.project_id));
    const archivedItems = loadedArchivedItems.filter((item) => !deletedProjectIdsRef.current.has(item.project_id));
    setProjects(items);
    setArchivedProjects(archivedItems);
    setSelectedProjectId((current) => {
      if (preferredId && items.some((item) => item.project_id === preferredId)) return preferredId;
      if (current && items.some((item) => item.project_id === current)) return current;
      return items[0]?.project_id ?? null;
    });
  }, []);

  const loadProject = useCallback(
    async (projectId: string, preferredChapterId?: string, signal?: AbortSignal) => {
      const revision = ++projectLoadRevisionRef.current;
      const [chapterItems, versionItems] = await Promise.all([
        api<ChapterWorkflow[]>("/api/projects/" + projectId + "/chapters", { signal }),
        api<VersionSummary[]>("/api/projects/" + projectId + "/versions", { signal }),
      ]);
      if (signal?.aborted || revision !== projectLoadRevisionRef.current || projectId !== selectedProjectIdRef.current) return;
      setChapters(chapterItems);
      setVersions(versionItems);
      const chapterId =
        preferredChapterId && chapterItems.some((item) => item.chapter_id === preferredChapterId)
          ? preferredChapterId
          : chapterItems.some((item) => item.chapter_id === selectedChapterIdRef.current)
            ? selectedChapterIdRef.current
            : chapterItems[0]?.chapter_id ?? null;
      setSelectedChapterId(chapterId);
      selectedChapterIdRef.current = chapterId;
    },
    []
  );

  useEffect(() => {
    const refreshInvalidatedProject = (event: Event) => {
      const projectId = (event as CustomEvent<{ projectId?: string }>).detail?.projectId;
      if (!projectId) return;
      if (selectedProjectIdRef.current === projectId) {
        void Promise.all([loadProjects(projectId), loadProject(projectId)]).catch(() => undefined);
      }
    };
    window.addEventListener("novel-writer:project-invalidated", refreshInvalidatedProject);
    return () => window.removeEventListener("novel-writer:project-invalidated", refreshInvalidatedProject);
  }, [loadProject, loadProjects]);

  const reconnectService = useCallback(async (signal: AbortSignal = new AbortController().signal) => {
    const health = await api<ServiceHealth>("/health", { signal });
    if (signal.aborted) return;
    if (health.status !== "ok") {
      setServiceState("unavailable");
      return;
    }
    const wasUnavailable = serviceState === "unavailable";
    if (wasUnavailable) {
      await loadProjects(undefined, signal);
      if (signal.aborted) return;
      if (selectedProjectIdRef.current) await loadProject(selectedProjectIdRef.current, undefined, signal);
      if (signal.aborted) return;
      setError(null);
      setNotice("应用服务连接已恢复；页面已从后端权威状态重新同步，没有重发任何写入或供应商请求。");
    }
    setServiceState("ready");
  }, [loadProject, loadProjects, serviceState]);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    void (async () => {
      try {
        const [health, projectItems, archivedItems] = await Promise.all([
          api<ServiceHealth>("/health", { signal: controller.signal }),
          api<ProjectSummary[]>("/api/projects", { signal: controller.signal }),
          api<ArchivedProjectSummary[]>("/api/projects/archived", { signal: controller.signal }),
        ]);
        if (!active) return;
        setProjects(projectItems);
        setArchivedProjects(archivedItems);
        const projectId = initialRoute.projectId && projectItems.some((item) => item.project_id === initialRoute.projectId)
          ? initialRoute.projectId
          : projectItems[0]?.project_id ?? null;
        setSelectedProjectId(projectId);
        selectedProjectIdRef.current = projectId;
        if (projectId) {
          await loadProject(projectId, initialRoute.chapterId ?? undefined, controller.signal);
          skipInitializedProjectLoadRef.current = projectId;
        }
        if (!active) return;
        setServiceState(health.status === "ok" ? "ready" : "unavailable");
        setRouteInitialized(true);
      } catch (caught: unknown) {
        if (!active) return;
        setServiceState("unavailable");
        setError(errorMessage(caught));
        setRouteInitialized(true);
      }
    })();
    return () => {
      active = false;
      controller.abort();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (serviceState === "checking") return;
    return startPolling({
      poll: reconnectService,
      intervalMs: serviceState === "unavailable" ? 3_000 : 15_000,
      immediate: false,
      onError: () => setServiceState("unavailable"),
    });
  }, [reconnectService, serviceState]);

  useEffect(() => {
    if (!routeInitialized) return;
    if (!selectedProjectId) {
      setChapters([]);
      setVersions([]);
      return;
    }
    if (skipInitializedProjectLoadRef.current === selectedProjectId) {
      skipInitializedProjectLoadRef.current = null;
      return;
    }
    const controller = new AbortController();
    setChapters([]);
    setVersions([]);
    void loadProject(selectedProjectId, undefined, controller.signal).catch((caught: unknown) => {
      if (!controller.signal.aborted) setError(errorMessage(caught));
    });
    return () => controller.abort();
  }, [routeInitialized, selectedProjectId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function createProject(
    _title: string,
    setup: ProjectSetupPayload,
    draftId: string,
  ) {
    setBusy(true);
    setError(null);
    try {
      const created = await api<{ project_id: string }>(`/api/project-setup-drafts/${draftId}/finalize`, {
        method: "POST",
        headers: commandHeaders(),
        body: jsonBody({
          confirmed: true,
          import_binding: setup.mode === "import" ? setup.import_preview : null,
        }),
      });
      await loadProjects(created.project_id);
      setDialog(null);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function removeSelectedChapter() {
    if (!selectedProjectId || !selectedChapterId) return;
    try {
      const preview = await api<{
        base_version: number;
        preview_sha256: string;
        affected_chapter_count: number;
        sessions_archived: number;
        runs_stopped: number;
      }>(`/api/chapters/${selectedChapterId}/truncate-preview`);
      setDangerDialog({
        title: `从第${chapters.find((item) => item.chapter_id === selectedChapterId)?.ordinal ?? 1}章开始截断后续章节`,
        summary: `当前正式 v${preview.base_version} 将创建新版本；${preview.affected_chapter_count} 个章节退出当前版本，${preview.sessions_archived} 个未完成批次归档，${preview.runs_stopped} 个自动运行停止。旧正文、版本和审计仍可恢复。`,
        confirmLabel: "确认截断并创建新版本",
        confirm: async () => {
          setBusy(true); setError(null); setNotice(null);
          try {
            const removed = await api<{ version: number; removed_chapter_count: number }>(`/api/chapters/${selectedChapterId}/truncate`, {
              method: "POST", headers: commandHeaders(),
              body: jsonBody({ confirmed: true, base_version: preview.base_version, preview_sha256: preview.preview_sha256, reason: "作者确认从本章截断后续章节" }),
            });
            setSelectedChapterId(null);
            publishProjectInvalidation(selectedProjectId, false);
            await Promise.all([loadProjects(selectedProjectId), loadProject(selectedProjectId)]);
            setNotice(`已创建正式 v${removed.version}，同步截断 ${removed.removed_chapter_count} 章；历史正文与审计仍保留。`);
          } finally { setBusy(false); }
        },
      });
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function saveFormalTitles(titles: Array<{ chapter_id: string; title: string }>) {
    if (!selectedProject) return;
    if (!await requestConfirmation("把这些标题保存为新的正式版本？正文和 StoryState 不会改变。")) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/api/projects/${selectedProject.project_id}/formal-chapter-titles`, {
        method: "PUT",
        headers: commandHeaders(),
        body: jsonBody({
          base_version: selectedProject.current_version,
          titles,
          reason: "作者手动调整正式章节标题",
          confirmed: true,
        }),
      });
      setEditingTitles(false);
      publishProjectInvalidation(selectedProject.project_id, false);
      await Promise.all([loadProjects(selectedProject.project_id), loadProject(selectedProject.project_id)]);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function rollbackToVersion(target: VersionSummary) {
    if (!selectedProjectId || !selectedProject || target.number === selectedProject.current_version) return;
    const accepted = await requestConfirmation({ title: "恢复正式版本", danger: true, confirmLabel: "创建恢复版本", message:
      `将《${selectedProject.title}》恢复到正式 v${target.number}（${target.chapter_count}章）？\n\n当前正式状态不会被覆盖或删除；系统会另建 v${selectedProject.current_version + 1}，完整恢复该版本的正文映射与 StoryState。未完成创作批次会归档，自动运行会终止，Provider 不会被调用。`,
    });
    if (!accepted) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const restored = await api<{ version: number; rollback_of: number; chapter_count: number }>(
        `/api/projects/${selectedProjectId}/rollback`,
        {
          method: "POST",
          headers: commandHeaders(),
          body: jsonBody({
            target_version: target.number,
            base_version: selectedProject.current_version,
            confirmed: true,
            reason: `作者从前端版本历史恢复 v${target.number}`,
          }),
        },
      );
      setSelectedChapterId(null);
      publishProjectInvalidation(selectedProjectId, false);
      await Promise.all([loadProjects(selectedProjectId), loadProject(selectedProjectId)]);
      setNotice(`已创建正式 v${restored.version}，内容恢复自 v${restored.rollback_of}（${restored.chapter_count}章）。`);
    } catch (caught) {
      setError(errorMessage(caught, "版本恢复失败"));
    } finally {
      setBusy(false);
    }
  }

  async function restartStory() {
    if (!selectedProjectId || !selectedProject) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const preview = await api<StoryRestartPreview>(
        `/api/projects/${selectedProjectId}/restart-story/preview`,
      );
      const accepted = await requestConfirmation({ title: "从头重新创作", danger: true, confirmLabel: "创建零章节新版本", message:
        `将《${selectedProject.title}》从头重新创作？\n\n系统会迁移当前正式 v${preview.setting_source_version} 的世界观、人物和大纲，创建新的零章节版本；当前 ${preview.chapter_count_to_clear} 章、${preview.scene_count_to_clear} 个场景和 ${preview.event_count_to_clear} 个事件将退出新版本。人物当前状态、剧情线进度和叙事位置也会原样迁移，请在重开后手动检查。\n\nv${preview.current_version}、旧正文、Provider 输出、费用和审计全部保留。该操作不调用模型。`,
      });
      if (!accepted) return;
      const restarted = await api<{ version: number; setting_source_version: number }>(
        `/api/projects/${selectedProjectId}/restart-story`,
        {
          method: "POST",
          headers: commandHeaders(),
          body: jsonBody({
            base_version: preview.current_version,
            confirmed: true,
            reason: "作者从版本页确认迁移当前正式世界观、人物和大纲并从头重新创作",
          }),
        },
      );
      setSelectedChapterId(null);
      publishProjectInvalidation(selectedProjectId, false);
      await Promise.all([loadProjects(selectedProjectId), loadProject(selectedProjectId)]);
      setNotice(`已创建正式 v${restarted.version}，已迁移 v${restarted.setting_source_version} 的当前设定并归零正文；请先手动检查人物状态、剧情线进度和叙事位置。`);
    } catch (caught) {
      setError(errorMessage(caught, "重新开书失败"));
    } finally {
      setBusy(false);
    }
  }

  async function pruneOldVersions() {
    if (!selectedProjectId || !selectedProject) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const preview = await api<VersionPrunePreview>(
        `/api/projects/${selectedProjectId}/versions/prune-preview`,
      );
      if (preview.deletable_versions.length === 0) {
        const protectedCount = preview.protected_versions.length;
        setNotice(
          preview.total_count <= 10
            ? "当前版本不超过 10 个，无需清理。"
            : `旧版本均被正文、会话或运行审计引用，当前没有可安全删除的版本（受保护 ${protectedCount} 个）。`,
        );
        return;
      }
      const accepted = await requestConfirmation({
        title: "永久清理无引用版本",
        message: `将永久删除 ${preview.deletable_versions.length} 个无引用旧版本，只保留最新 10 版及审计依赖版本。\n\n预计释放约 ${formatBytes(preview.estimated_reclaim_bytes)}。Revision、Provider 输出、费用与审计不会删除。`,
        requiredText: selectedProject.title,
        confirmLabel: "永久清理",
        danger: true,
      });
      if (!accepted) return;
      const pruned = await api<{ deleted_count: number; protected_versions: unknown[] }>(
        `/api/projects/${selectedProjectId}/versions/prune`,
        {
          method: "POST",
          headers: commandHeaders(),
          body: jsonBody({
            base_version: preview.current_version,
            keep_latest: 10,
            confirmed: true,
          confirmed_title: selectedProject.title,
            reason: "作者从版本页手动清理旧版本并保留最新10版",
          }),
        },
      );
      await loadProject(selectedProjectId);
      setNotice(`已删除 ${pruned.deleted_count} 个无引用旧版本；另有 ${pruned.protected_versions.length} 个审计依赖版本继续保留。`);
    } catch (caught) {
      setError(errorMessage(caught, "旧版本清理失败"));
    } finally {
      setBusy(false);
    }
  }

  async function synchronizeDeletedProjects(receipts: DeletionReceipt[]): Promise<DeletionReceipt[]> {
    for (const receipt of receipts) deletedProjectIdsRef.current.add(receipt.project_id);
    const deleted = deletedProjectIdsRef.current;
    setProjects((current) => current.filter((item) => !deleted.has(item.project_id)));
    setArchivedProjects((current) => current.filter((item) => !deleted.has(item.project_id)));
    if (selectedProjectIdRef.current && deleted.has(selectedProjectIdRef.current)) {
      projectLoadRevisionRef.current += 1;
      selectedProjectIdRef.current = null;
      selectedChapterIdRef.current = null;
      setSelectedProjectId(null); setSelectedChapterId(null);
      setChapters([]); setVersions([]);
    }
    const reconciled = await Promise.all(receipts.map(async (receipt) => {
      try {
        await deleteProjectBrowserData(receipt.project_id);
        return { ...receipt, browser_cleanup_error: undefined };
      } catch (caught) {
        return { ...receipt, browser_cleanup_error: caught instanceof Error ? caught.message : "本浏览器草稿清理失败" };
      }
    }));
    try { await loadProjects(); }
    catch (caught) { setError(errorMessage(caught, "删除已提交，但书架刷新失败；请刷新删除记录核对。")); }
    return reconciled;
  }

  async function handleProjectDeleted(receipt: DeletionReceipt) {
    setLatestDeletion(receipt);
    const [reconciled] = await synchronizeDeletedProjects([receipt]);
    setLatestDeletion(reconciled);
    setNotice(reconciled.browser_cleanup_error ? "服务端删除已提交，但本浏览器草稿清理失败，请在删除记录中重试。" : receipt.status === "completed"
      ? "整本小说已永久删除，清理完成；共享资源和外部备份未删除。"
      : "小说数据库记录已删除，但文件清理未完成。请在删除记录中查看原因并继续本地清理。");
  }

  async function archiveProject(projectId: string, title: string) {
    try {
      const preview = await api<{ formal_version: number; chapter_count: number; sessions_archived: number; runs_stopped: number }>(`/api/projects/${projectId}/archive-preview`);
      setDangerDialog({
        title: `归档作品“${title}”`,
        summary: `正式 v${preview.formal_version} 和 ${preview.chapter_count} 章正文不会删除。${preview.sessions_archived} 个未完成批次会归档，${preview.runs_stopped} 个自动运行会停止；作品之后可从归档列表恢复。`,
        confirmLabel: "确认归档作品",
        confirm: async () => {
          setBusy(true); setError(null);
          try {
            await api(`/api/projects/${projectId}/archive`, {
              method: "POST", headers: commandHeaders(),
              body: jsonBody({ confirmed: true, confirmed_title: title }),
            });
            if (selectedProjectId === projectId) {
              setSelectedProjectId(null); setSelectedChapterId(null); setChapters([]);
              setVersions([]);
            }
            await loadProjects();
          } finally { setBusy(false); }
        },
      });
    } catch (caught) { setError(errorMessage(caught)); }
  }

  async function restoreArchivedProject(projectId: string, title: string) {
    const accepted = await requestConfirmation({
      title: `恢复归档作品“${title}”`,
      message: "作品会重新出现在活动列表；正式正文和版本不变。归档前已终止的运行不会自动恢复，历史写作会话继续保留在审计中。",
      confirmLabel: "恢复作品",
      requiredText: title,
    });
    if (!accepted) return;
    setBusy(true); setError(null);
    try {
      await api(`/api/projects/${projectId}/restore`, {
        method: "POST",
        headers: commandHeaders(),
        body: jsonBody({ confirmed: true, confirmed_title: title }),
      });
      await loadProjects(projectId);
      setShowArchivedProjects(false);
      setNotice(`已恢复归档作品“${title}”；正式正文未变化，旧运行未自动重启。`);
    } catch (caught) { setError(errorMessage(caught, "恢复归档作品失败")); }
    finally { setBusy(false); }
  }

  const statusLabel = {
    checking: "正在检查",
    ready: "服务就绪",
    unavailable: "服务不可用",
  }[serviceState];

  return (
    <div className={"shell" + (chapterPaneOpen ? " chapter-pane-open" : "")}>
      <a className="skip-link" href="#main-workspace">跳至工作区</a>
      <aside className={"sidebar" + (libraryOpen ? " library-open" : "")} aria-label="作品书架">
        <div className="brand">
          <svg className="brand-mark" viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M16 8c-4-3-8-3-12-2v19c4-1 8-1 12 2 4-3 8-3 12-2V6c-4-1-8-1-12 2Zm0 0v19" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" /></svg>
          <div><strong>长篇小说工作台</strong><small>专注故事，安心创作</small></div>
        </div>
        <button className="library-toggle" type="button" aria-expanded={libraryOpen} aria-controls="project-library" onClick={() => setLibraryOpen((value) => !value)}>{libraryOpen ? "收起书架" : "作品书架"}<span aria-hidden="true">{libraryOpen ? "−" : "+"}</span></button>
        <div className="project-library" id="project-library">
        <div className="sidebar-heading">
          <span>作品（{projects.length}）</span>
          <button className="compact-command" type="button" onClick={() => setDialog("project")}>
            新建
          </button>
        </div>
        {projects.length > 4 && <input className="project-search" aria-label="搜索作品" value={projectQuery} onChange={(event) => setProjectQuery(event.target.value)} placeholder="搜索作品" />}
        <div className="project-list">
          {visibleProjects.map((project) => (
            <div className="project-row-actions" key={project.project_id}>
              <button
                className={"project-row" + (project.project_id === selectedProjectId ? " active" : "")}
                aria-current={project.project_id === selectedProjectId ? "true" : undefined}
                title={project.title}
                type="button" onClick={() => selectProject(project.project_id)}
              ><span>{project.title}</span><small>v{project.current_version}</small></button>
              <button className="danger-command" type="button" disabled={busy}
                aria-label={`归档作品 ${project.title}`}
                onClick={() => void archiveProject(project.project_id, project.title)}>归档</button>
              <ProjectDeleteButton projectId={project.project_id} title={project.title}
                disabled={busy} onResult={handleProjectDeleted} />
            </div>
          ))}
          {serviceState !== "checking" && visibleProjects.length === 0 && <small className="project-empty">没有匹配的作品</small>}
        </div>
        <button className="archived-project-toggle" type="button" aria-expanded={showArchivedProjects} onClick={() => setShowArchivedProjects((value) => !value)}>归档作品（{archivedProjects.length}）</button>
        {showArchivedProjects && <div className="archived-project-list">{archivedProjects.length === 0 ? <small className="project-empty">暂无归档作品</small> : archivedProjects.map((project) => <div className="archived-project-row" key={project.project_id}><div><strong>{project.title}</strong><small>v{project.current_version} · {new Date(project.archived_at).toLocaleDateString()}</small></div><button type="button" disabled={busy} onClick={() => void restoreArchivedProject(project.project_id, project.title)}>恢复</button><ProjectDeleteButton projectId={project.project_id} title={project.title} disabled={busy} onResult={handleProjectDeleted} /></div>)}</div>}
        <ProjectDeletionHistory latest={latestDeletion} onResult={handleProjectDeleted} onRefresh={synchronizeDeletedProjects} />
        </div>
        <div className={"service-status " + serviceState}>
          <span className="status-dot" aria-hidden="true" />
          {statusLabel}
        </div>
      </aside>

      <section className="chapter-pane" id="chapter-directory" aria-label="章节目录" hidden={!chapterPaneOpen} onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); closeChapterPane(); } }}>
        <div className="directory-heading"><strong>正式章节</strong><button ref={directoryCloseRef} type="button" aria-label="关闭章节目录" onClick={closeChapterPane}>×</button></div>
        <div className="pane-header">
          <span>章节 · {chapters.length}</span>
          <div className="pane-actions">
            <button className="compact-command" type="button" disabled={!selectedProject || chapters.length === 0 || busy} onClick={() => setEditingTitles((value) => !value)}>改标题</button>
            <button className="compact-command" type="button" disabled={!selectedChapterId || busy} onClick={() => void removeSelectedChapter()}>截断后续</button>
          </div>
        </div>
        {editingTitles && selectedProject && <FormalTitleEditor chapters={chapters} busy={busy} cancel={() => setEditingTitles(false)} save={saveFormalTitles} />}
        <div className="chapter-list">
          {chapters.length === 0 && <p className="chapter-empty">导入的正式章节会显示在这里。</p>}
          {chapters.map((chapter) => (
            <button
              className={
                "chapter-row" + (chapter.chapter_id === selectedChapterId ? " active" : "")
              }
              key={chapter.chapter_id}
              type="button"
              onClick={() => {
                const navigate = () => {
                  setSelectedChapterId(chapter.chapter_id);
                  closeChapterPane();
                  setViewModeState("reader");
                  navigateTo(routeHref({ projectId: selectedProjectId, view: "reader", chapterId: chapter.chapter_id }, location));
                };
                if (getDirtySurfaces().length > 0) setPendingNavigation(() => navigate);
                else navigate();
              }}
            >
              <span className="chapter-number">{String(chapter.ordinal).padStart(2, "0")}</span>
              <span className="chapter-copy">
                <strong>{chapter.title}</strong>
                <small className={"phase " + chapter.phase}>{chapter.phase === "committed" ? "已封存正文" : "待正式正文"}</small>
              </span>
            </button>
          ))}
        </div>
      </section>

      <main className="workspace" id="main-workspace" tabIndex={-1}
        onPointerOver={preloadNavigationTarget} onFocus={preloadNavigationTarget}>
        <header className="topbar">
          <div className="workspace-title">
            <div className="workspace-title-copy"><span className="eyebrow">我的创作空间</span><h1 title={selectedProject?.title}>{selectedProject?.title ?? "工作台"}</h1></div>
            {selectedProject && <span className="version-label">正式状态 v{selectedProject.current_version}</span>}
          </div>
          <GlobalSearch projectId={selectedProjectId} onNavigate={navigateSearchResult} />
          <GlobalRequestStatus locallyBusy={busy} projectId={selectedProjectId} />
          <div className="view-switch" role="navigation" aria-label="作品任务">
            <button
              className={viewMode === "home" ? "active" : ""}
              aria-current={viewMode === "home" ? "page" : undefined}
              type="button"
              onClick={() => setViewMode("home")}
            >
              工作台
            </button>
            <button
              className={viewMode === "blueprint" ? "active" : ""}
              data-workspace="blueprint"
              aria-current={viewMode === "blueprint" ? "page" : undefined}
              type="button"
              onClick={() => setViewMode("blueprint")}
            >
              故事资料
            </button>
            <button
              className={viewMode === "reader" ? "active" : ""}
              data-workspace="reader"
              aria-current={viewMode === "reader" ? "page" : undefined}
              type="button"
              onClick={() => setViewMode("reader")}
            >
              阅读正文
            </button>
            <button
              className={viewMode === "data" ? "active" : ""}
              data-workspace="data"
              aria-current={viewMode === "data" ? "page" : undefined}
              type="button"
              onClick={() => setViewMode("data")}
            >
              导入与导出
            </button>
            <button className={advancedMode ? "active" : ""} type="button" aria-expanded={advancedMode} onClick={() => setAdvancedMode((value) => !value)}>高级设置</button>
            {advancedMode && <div className="advanced-navigation" role="group" aria-label="高级工具">
              <button data-workspace="models" className={viewMode === "models" ? "active" : ""} aria-current={viewMode === "models" ? "page" : undefined} type="button" onClick={() => setViewMode("models")}>模型</button>
              <button data-workspace="longform" className={viewMode === "longform" ? "active" : ""} aria-current={viewMode === "longform" ? "page" : undefined} type="button" onClick={() => setViewMode("longform")}>长篇脉络</button>
              <button data-workspace="style" className={viewMode === "style" ? "active" : ""} aria-current={viewMode === "style" ? "page" : undefined} type="button" onClick={() => setViewMode("style")}>质量偏好</button>
              <button className={viewMode === "versions" ? "active" : ""} aria-current={viewMode === "versions" ? "page" : undefined} type="button" onClick={() => setViewMode("versions")}>版本维护</button>
            </div>}
          </div>
          <button ref={directoryToggleRef} className="directory-toggle" type="button" aria-expanded={chapterPaneOpen} aria-controls="chapter-directory" onClick={() => setChapterPaneOpen((value) => !value)}>{chapterPaneOpen ? "收起目录" : "章节目录"}<span>{chapters.length}</span></button>
        </header>

        {error && (
          <div className="error-banner" role="alert">
            <span>{error}</span>
            <button type="button" onClick={() => setError(null)}>关闭</button>
          </div>
        )}
        {notice && <div className="success-banner"><span>{notice}</span><button type="button" onClick={() => setNotice(null)}>关闭</button></div>}
        {serviceState === "unavailable" && (
          <div className="error-banner service-reconnect-banner" role="status">
            <span>应用服务暂时不可达。页面可见时会自动检测连接，恢复后刷新只读状态；写入请求和供应商调用不会自动重发。</span>
            <button type="button" onClick={() => void reconnectService().catch(() => undefined)}>立即重连</button>
          </div>
        )}

        <Suspense key={`${selectedProjectId}:${viewMode}`} fallback={<WorkspaceLoading />}>
          {serviceState === "checking" ? (
            <div className="management-empty"><strong>正在载入作品</strong><span>正在检查本地服务与正式故事状态。</span></div>
          ) : !selectedProject ? (
            <EmptyState title="尚无项目" action="新建项目" onAction={() => setDialog("project")} />
          ) : viewMode === "home" ? (
            <section className="empty-state author-home">
              <h2>当前作品</h2>
              <p>正式状态 v{selectedProject.current_version}，共 {chapters.length} 章。选择本阶段主导题材，设计并创作完整一章，再由你决定是否采用。</p>
              <div className="button-row">
                <button data-workspace="generation" type="button" onClick={() => setViewMode("generation")}>题材主导创作</button>
                <button data-workspace="reader" type="button" onClick={() => setViewMode("reader")}>阅读正式正文</button>
              </div>
            </section>
          ) : viewMode === "generation" ? (
            <GenerationWorkspace key={selectedProject.project_id} projectId={selectedProject.project_id} onAdopted={async () => {
              publishProjectInvalidation(selectedProject.project_id, false);
              await Promise.all([loadProjects(selectedProject.project_id), loadProject(selectedProject.project_id)]);
            }} />
          ) : viewMode === "models" ? (
            <ModelWorkspace />
          ) : viewMode === "blueprint" ? (
            <BlueprintWorkspace
              projectId={selectedProject.project_id}
              onSaved={async () => {
                publishProjectInvalidation(selectedProject.project_id, false);
                await Promise.all([loadProjects(selectedProject.project_id), loadProject(selectedProject.project_id)]);
              }}
            />
          ) : viewMode === "reader" ? (
            <ReaderView projectId={selectedProject.project_id} initialChapterId={selectedChapterId} />
          ) : viewMode === "longform" ? (
            <LongformWorkspace projectId={selectedProject.project_id} />
          ) : viewMode === "style" ? (
            <StyleWorkspace projectId={selectedProject.project_id} />
          ) : viewMode === "data" ? (
            <DataWorkspace
              projectId={selectedProject.project_id}
              projectTitle={selectedProject.title}
              onProjectCreated={async (projectId) => {
                await loadProjects(projectId);
                selectProject(projectId);
                setViewMode("data");
              }}
            />
          ) : viewMode === "versions" ? (
            <VersionHistory
              versions={versions}
              currentVersion={selectedProject.current_version}
              busy={busy}
              rollback={rollbackToVersion}
              restartStory={restartStory}
              pruneOldVersions={pruneOldVersions}
            />
          ) : (
            <EmptyState title="尚无正式章节" action="导入正文" onAction={() => setViewMode("data")} />
          )}
        </Suspense>
      </main>

      {dialog === "project" && (
        <ProjectDialog busy={busy} close={() => setDialog(null)} submit={createProject} />
      )}
      {pendingNavigation && (
        <div className="dialog-backdrop" role="presentation">
          <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="unsaved-dialog-title">
            <div className="dialog-header"><h2 id="unsaved-dialog-title">有未保存修改</h2></div>
            <p>以下内容尚未保存：{dirtySurfaces.map((item) => item.label).join("、") || "当前编辑内容"}。</p>
            <div className="dialog-actions">
              <button className="secondary-button" type="button" onClick={() => { if (blocker.state === "blocked") blocker.reset(); setPendingNavigation(null); }}>留在当前页面</button>
              <button className="danger-command" type="button" onClick={() => { const action = pendingNavigation; void discardDirtySurfaces().then(() => { setPendingNavigation(null); action?.(); }).catch((caught: unknown) => setError(errorMessage(caught))); }}>放弃修改</button>
              <button className="primary-button" type="button" onClick={() => { const action = pendingNavigation; setBusy(true); void saveDirtySurfaces().then(() => { setPendingNavigation(null); action?.(); }).catch((caught: unknown) => setError(errorMessage(caught, "保存未完成，已留在当前页面"))).finally(() => setBusy(false)); }}>保存后继续</button>
            </div>
          </section>
        </div>
      )}
      {dangerDialog && (
        <div className="dialog-backdrop" role="presentation">
          <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="danger-dialog-title">
            <div className="dialog-header"><h2 id="danger-dialog-title">{dangerDialog.title}</h2><button type="button" aria-label="关闭" onClick={() => setDangerDialog(null)}>×</button></div>
            <p>{dangerDialog.summary}</p>
            <div className="dialog-actions">
              <button className="secondary-button" type="button" onClick={() => setDangerDialog(null)}>取消</button>
              <button className="danger-command" type="button" disabled={busy} onClick={() => { const action = dangerDialog.confirm; setDangerDialog(null); void action().catch((caught: unknown) => setError(errorMessage(caught))); }}>{dangerDialog.confirmLabel}</button>
            </div>
          </section>
        </div>
      )}
      <ConfirmationHost />
    </div>
  );
}

function EmptyState({ title, action, onAction }: { title: string; action: string; onAction: () => void }) {
  return <section className="empty-state"><h2>{title}</h2><button className="primary-button" type="button" onClick={onAction}>{action}</button></section>;
}

function WorkspaceLoading({ label = "工作区" }: { label?: string }) {
  return <div className="workspace-loading" role="status">正在载入{label}，你可以继续切换页面。</div>;
}

function preloadNavigationTarget(event: SyntheticEvent<HTMLElement>) {
  if (event.target instanceof Element) {
    preloadWorkspace(event.target.closest<HTMLElement>("[data-workspace]")?.dataset.workspace);
  }
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function errorMessage(value: unknown, fallback = "操作失败"): string {
  return value instanceof Error && value.message.trim() ? value.message : fallback;
}
