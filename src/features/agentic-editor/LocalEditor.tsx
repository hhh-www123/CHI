import { useEffect, useRef, useState, type HTMLAttributes, type MouseEvent, type KeyboardEvent } from 'react';
import { Check, Code2, LockKeyhole, MousePointer2, ShieldCheck, UnlockKeyhole, X } from 'lucide-react';
import { api } from '../../api';
import type { Session } from '../../types';
import type { DisplayChange } from './historyTypes';
import type { ComponentBinding, EditorField, EditorScope, PatchPreview } from './types';

export const PERIOD_LABELS = { am: '上午', pm: '下午', evening: '晚上' } as const;
const FIELD_LABELS = { description: '景点描述', startTime: '开始时间', placeId: '真实景点' } as const;
const DEMO_DESCRIPTION = '减少步行，安排一次休息，按自己的节奏游览。';

export function bindingFor(session: Session | null, dayKey: string, stopId?: string, period?: string) {
  return Object.values(session?.componentRegistry ?? {}).find(binding =>
    binding.dayKey === dayKey && (stopId ? binding.stopId === stopId : !binding.stopId && (period ? binding.period === period : !binding.period)));
}
export function isComponentLocked(session: Session, componentId?: string, includeLegacy = true): boolean {
  if (!componentId) return false;
  const seen = new Set<string>();
  let current: string | undefined = componentId;
  while (current && !seen.has(current)) {
    seen.add(current);
    if (session.persistedConstraints?.lockedComponentIds.includes(current)) return true;
    current = session.componentRegistry?.[current]?.parentId;
  }
  const binding = session.componentRegistry?.[componentId];
  return includeLegacy && !!binding && session.controlState.lockedRegions.includes(binding.regionId);
}
type SelectableAttributes = HTMLAttributes<HTMLElement> & {
  'data-component-id'?: string;
  'data-editor-selected'?: string;
  'data-editor-locked'?: string;
  'data-editor-label'?: string;
};
export function selectionAttributes(binding: ComponentBinding | undefined, mode: boolean, selected: string | null, locked: boolean, onSelect: (id: string) => void): SelectableAttributes {
  if (!binding) return {};
  const select = (event: MouseEvent<HTMLElement>) => {
    if (!mode) return;
    const target = event.target as HTMLElement;
    if (target.closest('button, a, input, select, textarea, label, [contenteditable="true"]')) return;
    if (target.closest('[data-component-id]') !== event.currentTarget) return;
    event.stopPropagation();
    onSelect(binding.componentId);
  };
  const keySelect = (event: KeyboardEvent<HTMLElement>) => {
    if (!mode || event.target !== event.currentTarget || !['Enter', ' '].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation(); onSelect(binding.componentId);
  };
  return {
    'data-component-id': binding.componentId,
    'data-editor-label': binding.label,
    'data-editor-selected': mode && selected === binding.componentId ? 'true' : undefined,
    'data-editor-locked': locked ? 'true' : undefined,
    tabIndex: mode ? 0 : undefined,
    onClick: select,
    onKeyDown: keySelect,
  };
}

interface Props {
  session: Session;
  mode: boolean;
  selectedId: string | null;
  busy: boolean;
  onModeChange: (mode: boolean) => void;
  onSelect: (id: string) => void;
  onLock: (componentId: string, locked: boolean) => void;
  onApply: (previewId: string, baseVersion: number) => Promise<boolean>;
  onDisplay: (componentId: string, change: DisplayChange) => Promise<boolean>;
  onError: (error: unknown) => void;
}
export function LocalEditor({ session, mode, selectedId, busy, onModeChange, onSelect, onLock, onApply, onDisplay, onError }: Props) {
  const previewEpoch = useRef(0);
  const [field, setField] = useState<EditorField>('description');
  const [activityId, setActivityId] = useState('');
  const [value, setValue] = useState('');
  const [scope, setScope] = useState<EditorScope | null>(null);
  const [scopeError, setScopeError] = useState('');
  const [pending, setPending] = useState(false);
  const [preview, setPreview] = useState<PatchPreview | null>(null);
  const [report, setReport] = useState('');
  const binding = selectedId ? session.componentRegistry?.[selectedId] : undefined;
  const ownLock = !!selectedId && !!session.persistedConstraints?.lockedComponentIds.includes(selectedId);
  const activities = session.tripDocument?.entities.activities ?? {};
  const selectedActivity = activities[activityId];
  const dayBindings = Object.values(session.componentRegistry ?? {}).filter(item => !item.stopId && !item.period);

  useEffect(() => {
    previewEpoch.current += 1;
    setPreview(null); setReport(''); setScope(null); setScopeError('');
    if (!mode || !selectedId) { setPending(false); return; }
    const controller = new AbortController();
    setPending(true);
    api.editorScope(session.id, session.version, selectedId, [field], controller.signal)
      .then(next => {
        if (controller.signal.aborted) return;
        setScope(next);
        setActivityId(current => next.eligibleActivityIds.includes(current) ? current : next.eligibleActivityIds[0] ?? '');
      })
      .catch(error => { if (!controller.signal.aborted) setScopeError(error instanceof Error ? error.message : '无法读取服务端范围。'); })
      .finally(() => { if (!controller.signal.aborted) setPending(false); });
    return () => controller.abort();
  }, [session.id, session.version, selectedId, field, mode]);
  useEffect(() => {
    setValue(selectedActivity?.[field] ?? '');
    setPreview(null);
  }, [selectedActivity?.id, selectedActivity?.description, selectedActivity?.startTime, selectedActivity?.placeId, field, selectedId, session.version]);

  const allowed = !!scope?.allowedPaths.length && !scope.locked && !!selectedActivity;
  async function validatePreview() {
    if (!selectedId || !scope || !selectedActivity || !allowed) return;
    const path = '/entities/activities/' + activityId + '/' + field;
    if (!scope.allowedPaths.includes(path)) { setReport('当前字段不在服务端授权范围内。'); return; }
    const epoch = previewEpoch.current;
    setPending(true); setPreview(null); setReport('');
    try {
      const result = await api.editorPreview(session.id, session.version, selectedId, [field], [{ op: 'replace', path, value }]);
      if (previewEpoch.current !== epoch) return;
      setPreview(result);
      setReport('补丁已通过服务端校验；正式行程尚未修改。');
    } catch (error) { if (previewEpoch.current === epoch) setReport(error instanceof Error ? error.message : '补丁校验失败，正式状态保持不变。'); }
    finally { if (previewEpoch.current === epoch) setPending(false); }
  }
  async function applyPreview() {
    if (!preview || preview.baseVersion !== session.version) return;
    const ok = await onApply(preview.previewId, preview.baseVersion);
    if (ok) { setPreview(null); setReport('已应用本次局部修改。'); }
  }
  const previewActivity = preview?.document.entities.activities[activityId];
  return <section className="local-editor-panel" aria-label="局部编辑与可信修改范围" data-testid="local-editor-panel">
    <div className="local-editor-heading"><span><MousePointer2 size={16} />局部编辑</span><small>手工 Patch · 可信范围</small></div>
    <div className="editor-mode-switch" role="group" aria-label="页面浏览编辑模式">
      <button type="button" aria-pressed={!mode} onClick={() => onModeChange(false)} disabled={busy}>浏览模式</button>
      <button type="button" aria-pressed={mode} onClick={() => onModeChange(true)} disabled={busy} data-testid="enable-edit-mode">编辑模式</button>
    </div>
    <p className="editor-explanation">{mode ? '点击日期区域、时段或景点卡片选中。输入框、下拉框和地图仍可正常使用；也可用 Tab、Enter 选择。' : '浏览时保留原有交互。开启编辑模式后，可以选择并锁定局部组件。'}</p>
    <div className="editor-days" aria-label="日期选区与锁定">{dayBindings.map(item => {
      const locked = !!session.persistedConstraints?.lockedComponentIds.includes(item.componentId);
      const legacyLocked = session.controlState.lockedRegions.includes(item.regionId);
      return <div className="editor-day-row" key={item.componentId}>
        <button type="button" className={selectedId === item.componentId ? 'is-active' : ''} onClick={() => { onModeChange(true); onSelect(item.componentId); }} disabled={busy} aria-label={'选择' + item.label}>{item.label}</button>
        <button type="button" onClick={() => onLock(item.componentId, !locked)} disabled={busy || pending} aria-label={(locked ? '解锁' : '锁定') + item.label} aria-pressed={locked}>{locked ? <LockKeyhole size={13} /> : <UnlockKeyhole size={13} />}{locked ? '已锁定' : legacyLocked ? '另有生成锁' : '锁定'}</button>
      </div>;
    })}</div>
    <div className="editor-selection" aria-live="polite"><span>当前版本 v{session.version}</span><strong>{mode && binding ? binding.label : '尚未选择组件'}</strong>{mode && binding && <code>{binding.componentId}</code>}</div>
    {!mode || !binding ? <p className="editor-empty-scope"><ShieldCheck size={15} />当前没有修改权限。请先开启编辑模式并选择组件；不会默认授权整个页面。</p> : <>
      <div className="editor-selection-actions"><button className="button secondary" type="button" disabled={busy || pending} onClick={() => onLock(binding.componentId, !ownLock)}>{ownLock ? <UnlockKeyhole size={14} /> : <LockKeyhole size={14} />}{ownLock ? '解除本组件锁定' : '锁定此组件'}</button>{binding.parentId && <button className="button ghost" type="button" onClick={() => onSelect(binding.parentId!)} disabled={busy}>选择上级区域</button>}</div>
      {binding.kind === 'DayCard' ? <section className="editor-display-controls" data-testid="selected-day-display"><h3>所选整日的展示设置</h3><p>仅修改{binding.label}共享的卡片展示；景点、时间、预算与其他日期保持。</p><div><button className="button secondary" type="button" data-testid="display-cards" disabled={busy || pending || isComponentLocked(session, binding.componentId) || session.uiSpec.regions[binding.regionId].view === 'cards'} onClick={() => void onDisplay(binding.componentId, { view: 'cards' })}>卡片展示</button><button className="button secondary" type="button" data-testid="display-timeline" disabled={busy || pending || isComponentLocked(session, binding.componentId) || session.uiSpec.regions[binding.regionId].view === 'timeline'} onClick={() => void onDisplay(binding.componentId, { view: 'timeline' })}>时间轴展示</button><button className="button secondary" type="button" data-testid={session.uiSpec.regions[binding.regionId].compact ? 'expand-selected-day' : 'compact-selected-day'} disabled={busy || pending || isComponentLocked(session, binding.componentId)} onClick={() => void onDisplay(binding.componentId, { compact: !session.uiSpec.regions[binding.regionId].compact })}>{session.uiSpec.regions[binding.regionId].compact ? '显示完整卡片' : '精简卡片信息'}</button></div></section> : <p className="editor-display-hint">卡片风格是整日共享设置；如需修改，请先选择整天，不会从当前子选区扩大权限。</p>}
      <label className="field-label" htmlFor="editor-field">本次选定字段</label>
      <select id="editor-field" value={field} disabled={pending || busy} onChange={event => { setField(event.target.value as EditorField); setPreview(null); }}><option value="description">景点描述</option><option value="startTime">开始时间</option><option value="placeId">真实景点</option></select>
      {pending && !preview && <p className="editor-status" role="status">正在请求服务端校验…</p>}
      {scopeError && <p className="editor-denied" role="alert">{scopeError}</p>}
      <div className="editor-effective-scope" data-testid="effective-scope"><h3><ShieldCheck size={14} />当前有效修改范围</h3>
        {scope?.allowedPaths.length ? <><p>{scope.label || binding.label} · {FIELD_LABELS[field]}</p><small>{scope.eligibleActivityIds.length} 个活动可作为目标，最终以服务端白名单为准。</small></> : <p>{scope?.blockedReason || '请选择包含活动的未锁定日期、时段或景点卡片；空时段没有可修改活动。'}</p>}
        {scope?.locked && <p className="editor-denied">选区或上级组件已锁定，修改被禁止。</p>}
        {!!scope?.allowedPaths.length && <details><summary>查看服务端字段白名单</summary><ul>{scope.allowedPaths.map(path => <li key={path}><code>{path}</code></li>)}</ul></details>}
        <p className="editor-protected">保护范围：选区之外、未勾选字段、预算、锁定及内部版本信息。更换地点会同步真实名称；跨时段调整会同步卡片归属。</p>
      </div>
      {allowed && <div className="editor-patch-form">
        <label className="field-label" htmlFor="editor-activity">选择要修改的景点</label><select id="editor-activity" value={activityId} disabled={busy || pending} onChange={event => { setActivityId(event.target.value); setPreview(null); }}>{scope!.eligibleActivityIds.map(id => <option value={id} key={id}>{activities[id]?.startTime} · {activities[id]?.title ?? id}</option>)}</select>
        <label className="field-label" htmlFor="editor-value">{FIELD_LABELS[field]}</label>
        {field === 'description' ? <textarea id="editor-value" rows={3} maxLength={2000} value={value} onChange={event => { setValue(event.target.value); setPreview(null); }} disabled={busy || pending} /> : field === 'startTime' ? <input id="editor-value" type="time" value={value} onChange={event => { setValue(event.target.value); setPreview(null); }} disabled={busy || pending} /> : <select id="editor-value" value={value} onChange={event => { setValue(event.target.value); setPreview(null); }} disabled={busy || pending}>{(session.catalog?.places ?? []).filter(place => place.id === selectedActivity?.placeId || !session.tripState.days[binding.dayKey]?.stops.some(stop => stop.placeId === place.id && stop.id !== session.componentRegistry?.['card-' + activityId]?.stopId)).map(place => <option key={place.id} value={place.id}>{place.name} · {place.indoor ? '室内' : '户外'}</option>)}</select>}
        {field === 'description' && <button className="editor-fill-demo" type="button" disabled={busy || pending} onClick={() => { setValue(DEMO_DESCRIPTION); setPreview(null); }}>填入固定演示文本</button>}
        <button type="button" className="button primary" data-testid="validate-local-patch" disabled={busy || pending || value === selectedActivity?.[field]} onClick={() => void validatePreview()}><Code2 size={15} />校验并预览 Patch</button>
      </div>}
      {report && <p className="editor-status" role="status" data-testid="patch-validation-report">{report}</p>}
      {preview && previewActivity && <section className="editor-patch-preview" aria-label="局部补丁只读预览" data-testid="local-patch-preview">
        <span className="mini-eyebrow">只读草稿 · 尚未应用</span><article className="stop-card"><div className="stop-content"><h4>{previewActivity.title}</h4><p className="stop-meta">{previewActivity.startTime}</p><p className="stop-description">{previewActivity.description}</p></div></article>
        <p>修改字段：{FIELD_LABELS[field]}{preview.derivedChangedPaths?.length ? `，另有 ${preview.derivedChangedPaths.length} 个名称或归属字段自动同步` : ''}。左侧仍是正式行程。</p>
        <div className="editor-preview-actions"><button className="button primary" type="button" data-testid="apply-local-patch" disabled={busy || pending || preview.baseVersion !== session.version} onClick={() => void applyPreview().catch(onError)}><Check size={14} />确认应用局部修改</button><button className="button ghost" type="button" onClick={() => { setPreview(null); setReport('已放弃草稿，正式状态保持不变。'); }} disabled={busy}><X size={14} />放弃</button></div>
      </section>}
    </>}
    <p className="editor-phase-note">手工补丁与下方自然语言候选共用服务端守卫；模型不能扩大选区权限，确认采用前不保存。</p>
  </section>;
}
