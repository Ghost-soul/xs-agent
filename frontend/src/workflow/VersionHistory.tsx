import type { VersionSummary } from "../api";

export function VersionHistory({ versions, currentVersion, busy, rollback, restartStory, pruneOldVersions }: { versions: VersionSummary[]; currentVersion: number; busy: boolean; rollback: (version: VersionSummary) => void; restartStory: () => void; pruneOldVersions: () => void }) {
  return (
    <section className="version-view">
      <div className="section-title"><div><span className="eyebrow">正式状态历史</span><h2>恢复不会覆盖历史</h2><p>选择任意旧版本会创建一个新的回退版本，并同步正文、StoryState 与未完成创作阶段；不会调用 Provider。</p></div></div>
      <div className="version-maintenance-actions">
        <div><strong>从头重新创作</strong><p>迁移当前正式世界观、人物和大纲，清空正文证据；人物状态与大纲进度保留，重开后可手动调整。</p></div>
        <button className="primary-button" type="button" disabled={busy} onClick={restartStory}>按当前设定重新开书</button>
        <div><strong>版本存储清理</strong><p>永久删除超过最新 10 版且没有被正文、会话或运行审计引用的版本。</p></div>
        <button type="button" disabled={busy || versions.length <= 10} onClick={pruneOldVersions}>清理旧版本</button>
      </div>
      <div className="version-table-header">
        <span>版本</span><span>章节</span><span>类型</span><span>创建时间</span><span>操作</span>
      </div>
      {versions.map((version) => (
        <div className="version-row" key={version.version_id}>
          <strong>v{version.number}</strong>
          <span>{version.chapter_count}</span>
          <span>{version.restart_of_number ? "重开" : version.rollback_of_number ? "回退" : version.parent_number ? "提交" : "初始"}</span>
          <time>{formatDate(version.created_at)}</time>
          {version.number === currentVersion
            ? <strong className="status-ready">当前正式</strong>
            : <button type="button" disabled={busy} onClick={() => rollback(version)}>恢复为新版本</button>}
        </div>
      ))}
    </section>
  );
}


function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}
