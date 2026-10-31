import { useEffect, useRef, useState } from 'react';
import { Check, History, LoaderCircle, RotateCcw, ShieldCheck } from 'lucide-react';
import { api } from '../../api';
import type { Session } from '../../types';
import type { VersionDiff, VersionSummary } from './historyTypes';

const kinds: Record<string, string> = { candidate_commit: '采用候选', restricted_patch: '局部补丁', presentation: '展示调整', display: '局部样式', component_lock: '组件锁', lock: '生成锁', constraints: '约束设置', constraint_change: '约束设置', global_presentation: '全局展示', restore: '恢复快照', history_restore: '恢复快照', local_display: '局部样式', undo: '撤销', create: '初始版本', checkpoint: '接入检查点', direct: '手动编辑', reset: '重置' };
const valueText = (value: unknown) => value === undefined ? '（不存在）' : typeof value === 'string' ? value || '（空）' : JSON.stringify(value);
function ConstraintSummary({ entry }: { entry: VersionSummary }) {
  const constraints = entry.constraintsSnapshot;
  const cap = constraints?.hardConstraints.find(item => item.active && item.kind === 'max_budget');
  const timeOrder = constraints?.hardConstraints.some(item => item.active && item.kind === 'time_order');
  return <span className="version-constraint-summary"><ShieldCheck size={12} />{constraints?.lockedComponentIds.length ?? 0} 个组件锁 · {cap ? `预算输入上限 ¥${Number(cap.value).toLocaleString('zh-CN')}` : '未设置预算硬上限'} · {timeOrder ? '时间顺序受保护' : '此快照未记录启用的时间约束'}</span>;
}
export function VersionHistory({ session, busy, onRestore }: { session: Session; busy: boolean; onRestore: (targetVersion: number, baseVersion: number) => Promise<boolean> }) {
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [serverVersion, setServerVersion] = useState(session.version);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<number | null>(null);
  const [diff, setDiff] = useState<VersionDiff | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [confirmation, setConfirmation] = useState<{ targetVersion: number; baseVersion: number } | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [pending, setPending] = useState(false);
  const diffController = useRef<AbortController | null>(null);
  const restorePanel = useRef<HTMLElement | null>(null);
  useEffect(() => { if (confirmation) requestAnimationFrame(() => restorePanel.current?.scrollIntoView({ block: 'nearest' })); }, [confirmation]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(''); setDiff(null); setSelected(null); setConfirmation(null); setConfirmed(false);
    diffController.current?.abort();
    api.versions(session.id, controller.signal).then(result => { if (!controller.signal.aborted) { setVersions([...result.versions].sort((a, b) => b.version - a.version)); setServerVersion(result.currentVersion); } })
      .catch(value => { if (!controller.signal.aborted) setError(value instanceof Error ? value.message : '版本记录读取失败，请重试。'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [session.id, session.version]);
  useEffect(() => () => { diffController.current?.abort(); }, []);
  async function showDiff(version: number) {
    diffController.current?.abort();
    const controller = new AbortController(); diffController.current = controller;
    setSelected(version); setDiff(null); setDiffLoading(true); setError('');
    try { const result = await api.versionDiff(session.id, version, session.version, controller.signal); if (!controller.signal.aborted) setDiff(result); }
    catch (value) { if (!controller.signal.aborted) setError(value instanceof Error ? value.message : '读取差异失败。'); }
    finally { if (!controller.signal.aborted) setDiffLoading(false); }
  }
  async function restore() {
    if (!confirmation || !confirmed || busy || pending) return;
    if (confirmation.baseVersion !== session.version) { setError(`当前版本已变为 v${session.version}，请重新选择恢复目标。`); setConfirmation(null); return; }
    setPending(true); setError('');
    try { const ok = await onRestore(confirmation.targetVersion, confirmation.baseVersion); if (!ok) setError('本次恢复未保存，请查看冲突提示并重新打开版本确认。'); }
    finally { setPending(false); }
  }
  return <section className="version-history" data-testid="version-history" aria-label="持久化版本历史">
    <p className="drawer-intro">当前版本 <strong>v{session.version}</strong> · 历史按版本保存行程、展示和约束。恢复会创建新版本，保留已有记录。</p>
    {serverVersion !== session.version && <p className="phase2-error" role="alert">页面基于 v{session.version}，服务端已为 v{serverVersion}。请先刷新同步；旧版本恢复请求会被拒绝。</p>}
    {loading && <p className="version-loading" role="status"><LoaderCircle size={15} className="spin" />正在读取已保存版本…</p>}
    {error && <p className="phase2-error" role="alert">{error}</p>}
    <div className="version-entries">{versions.map(entry => <article className="version-entry" key={entry.version} data-testid={'version-row-' + entry.version}>
      <div className="version-entry-heading"><strong>v{entry.version}{entry.version === session.version ? ' · 当前' : ''}</strong><span>{kinds[entry.kind] || entry.kind}</span></div>
      <small>{new Date(entry.createdAt).toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' })}{entry.parentVersion != null ? ` · 来自 v${entry.parentVersion}` : ' · 接入起点'}</small>
      <p>{entry.summary}</p>
      {entry.userRequest && entry.userRequest !== entry.summary && <p className="version-user-request">要求：{entry.userRequest}</p>}
      <ConstraintSummary entry={entry} />
      <span className="version-change-count">{entry.changedPaths.length} 个持久字段变化{entry.restoreFromVersion != null ? ` · 恢复来源 v${entry.restoreFromVersion}` : ''}</span>
      <div className="version-entry-actions"><button className="button secondary" type="button" data-testid={'version-diff-' + entry.version} disabled={busy || pending} onClick={() => void showDiff(entry.version)}>查看差异</button><button className="button ghost" type="button" data-testid={'restore-version-' + entry.version} disabled={busy || pending || entry.version === session.version} onClick={() => { setConfirmation({ targetVersion: entry.version, baseVersion: session.version }); setConfirmed(false); setError(''); }}><RotateCcw size={13} />恢复此版本</button></div>
      {selected === entry.version && <section className="version-diff" data-testid="version-diff" aria-label="版本字段差异">
        {diffLoading ? <p role="status">正在计算真实字段差异…</p> : diff && <><strong>v{diff.fromVersion} → v{diff.toVersion} · {diff.changedPaths.length} 项差异</strong><p>以下为两个已保存快照的持久数据差异；不代表像素或布局位置的变化。</p>{diff.differences.length ? <ul>{diff.differences.map(item => <li key={item.path}><code>{item.path}</code><del><small>v{diff.fromVersion}：</small>{valueText(item.before)}</del><ins><small>v{diff.toVersion}：</small>{valueText(item.after)}</ins></li>)}</ul> : <p>这两个快照的持久状态一致。</p>}</>}
        <details><summary>本版本授权与验证记录</summary><p>候选：{entry.candidateId || '明确的用户操作'} · 基于 v{entry.baseVersion ?? entry.version}</p><ul className="version-paths">{entry.allowedPaths.map(path => <li key={path}><code>{path}</code></li>)}</ul><p>{entry.kind === 'checkpoint' || entry.validationReport?.historicalValidationReplayed === false || entry.validationReport?.source === 'existing_state_checkpoint' ? '旧状态接入检查点，未重放历史验证' : entry.validationReport?.valid === true ? '服务端验证通过' : '接入快照或操作记录，以原始记录为准'}</p></details>
      </section>}
    </article>)}</div>
    {!loading && !versions.length && <div className="empty-state"><History size={24} /><p>尚无可恢复的持久版本。</p></div>}
    {confirmation && <section ref={restorePanel} className="version-restore-confirm" data-testid="restore-confirm-dialog" role="alertdialog" aria-label="确认恢复保存快照">
      <h3>恢复到 v{confirmation.targetVersion}？</h3><p>以 v{confirmation.baseVersion} 为当前版本，将全部行程、展示设置、组件锁及预算和时间约束恢复为 v{confirmation.targetVersion} 的快照，并创建 v{confirmation.baseVersion + 1}。这可能解除当前锁定或恢复旧锁；原历史不会删除。</p>
      <label><input type="checkbox" data-testid="confirm-restore-semantics" checked={confirmed} disabled={busy || pending} onChange={event => setConfirmed(event.target.checked)} />我确认同步恢复数据、展示与全部锁定/约束。</label>
      <div className="dialog-actions"><button type="button" className="button secondary" disabled={busy || pending} onClick={() => setConfirmation(null)}>取消恢复</button><button type="button" className="button primary" data-testid="confirm-restore-version" disabled={!confirmed || busy || pending} onClick={() => void restore()}><Check size={14} />确认恢复并创建新版本</button></div>
    </section>}
  </section>;
}
