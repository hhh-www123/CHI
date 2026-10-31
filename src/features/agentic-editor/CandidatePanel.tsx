import { parseSurface } from './protocol';
import { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, LoaderCircle, ShieldCheck, Sparkles, X } from 'lucide-react';
import { api } from '../../api';
import { StopCardContent } from '../../components/StopCardContent';
import type { Catalog, Session, TripStop } from '../../types';
import type { CandidateAgentMode, CandidateAgentStatus, CandidateBatch, CandidateStrategy, EditCandidate } from './candidateTypes';
import type { EditorField, EditorScope } from './types';

const STRATEGIES: Record<CandidateStrategy, { letter: string; label: string }> = {
  minimal_change: { letter: 'A', label: '最小改动' }, efficiency_first: { letter: 'B', label: '效率优先' }, experience_first: { letter: 'C', label: '体验优先' },
};
const FIELD_LABELS = { description: '景点描述', startTime: '开始时间', placeId: '真实景点', title: '景点名称（随地点同步）', period: '所属时段（随时间同步）', parentId: '卡片分组（随时间同步）' };
const PERIOD_LABELS = { am: '上午', pm: '下午', evening: '晚上' };

function CandidatePreview({ candidate, batch, session, catalog }: { candidate: EditCandidate; batch: CandidateBatch; session: Session; catalog: Catalog }) {
  const registry = Object.values(session.componentRegistry ?? {});
  const activities = candidate.document.entities.activities;
  const fallbackIds = candidate.document.dayOrder.flatMap(id => candidate.document.entities.days[id].activityIds).filter(id => batch.effectiveScope.eligibleActivityIds.includes(id));
  const ids = candidate.a2ui ? parseSurface(candidate.a2ui,candidate,batch.effectiveScope.eligibleActivityIds) : fallbackIds;
  const binding = registry.find(item => item.entityId === ids[0] && item.stopId);
  const ui = binding ? session.uiSpec.regions[binding.regionId] : undefined;
  return <section className="phase2-real-preview" data-testid="phase2-candidate-preview" data-candidate-id={candidate.candidateId} data-strategy={candidate.strategy} aria-label={'方案' + STRATEGIES[candidate.strategy].letter + '真实组件只读预览'}>
    <div className="phase2-preview-heading"><strong>方案 {STRATEGIES[candidate.strategy].letter} · 实际卡片预览</strong><span><ShieldCheck size={12} />只读 · 尚未采用</span></div>
    <div className={`stops-list ${ui?.view === 'timeline' ? 'timeline' : 'cards'} ${ui?.compact ? 'compact' : ''}`}>
      {ids.map((id, index) => {
        const item = activities[id];
        const original = registry.find(entry => entry.entityId === id && entry.stopId);
        const stop: TripStop = { id: original?.stopId ?? item.id, placeId: item.placeId, startTime: item.startTime, description: item.description };
        const place = catalog.places.find(value => value.id === stop.placeId);
        return <article className={`stop-card ${place?.indoor ? 'indoor' : 'outdoor'}`} key={id} data-activity-id={id} data-stop-id={stop.id}>
          <div className="stop-time"><span className="timeline-dot" /><span className="preview-stop-time">{stop.startTime}</span><span className="stop-index">停留 {String(index + 1).padStart(2, '0')}</span></div>
          <StopCardContent stop={stop} place={place} showCost={ui?.showCost ?? true} compact={ui?.compact ?? false} readOnly />
        </article>;
      })}
    </div>
    <p className="phase2-readonly-note">这里复用正式景点卡片；预览不提供编辑、外链或地图查询。左侧仍是已保存的行程。</p>
  </section>;
}

export function CandidatePanel({ session, catalog, selectedId, editorMode, busy, hasDrafts, onCommit, onConflict }: {
  session: Session; catalog: Catalog; selectedId: string | null; editorMode: boolean; busy: boolean; hasDrafts: boolean;
  onCommit: (candidateId: string, baseVersion: number) => Promise<boolean>; onConflict: (error: unknown) => void;
}) {
  const [instruction, setInstruction] = useState('');
  const [agentMode, setAgentMode] = useState<CandidateAgentMode>('mock');
  const [status, setStatus] = useState<CandidateAgentStatus | null>(null);
  const [fields, setFields] = useState<EditorField[]>(['description', 'startTime']);
  const [scope, setScope] = useState<EditorScope | null>(null);
  const [batch, setBatch] = useState<CandidateBatch | null>(null);
  const [candidateId, setCandidateId] = useState('');
  const [progress, setProgress] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [scopePending, setScopePending] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [expired, setExpired] = useState(false);
  const choseMode = useRef(false);
  const generation = useRef(0);
  const activeRequest = useRef<AbortController | null>(null);
  const binding = selectedId ? session.componentRegistry?.[selectedId] : undefined;
  const candidate = batch?.candidates.find(item => item.candidateId === candidateId);
  const fieldKey = fields.join(',');

  useEffect(() => {
    const controller = new AbortController();
    api.candidateStatus(controller.signal).then(next => { if (controller.signal.aborted) return; setStatus(next); if (!choseMode.current) setAgentMode(next.defaultMode); }).catch(() => {});
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!candidate) { setExpired(false); return; }
    const remaining = Date.parse(candidate.expiresAt) - Date.now();
    setExpired(remaining <= 0);
    if (remaining <= 0) return;
    const timer = setTimeout(() => setExpired(true), Math.min(remaining + 5, 2147483647));
    return () => clearTimeout(timer);
  }, [candidate?.candidateId, candidate?.expiresAt]);
  function discard(message = '') {
    generation.current += 1; activeRequest.current?.abort();
    setPending(false); setBatch(null); setCandidateId(''); setError(''); setNotice(message);
  }
  useEffect(() => {
    generation.current += 1; activeRequest.current?.abort();
    setPending(false); setBatch(null); setCandidateId(''); setError(''); setNotice(''); setScope(null);
    if (!editorMode || !selectedId || !fields.length) { setScopePending(false); return; }
    const controller = new AbortController();
    setScopePending(true);
    api.editorScope(session.id, session.version, selectedId, fields, controller.signal).then(value => { if (!controller.signal.aborted) setScope(value); })
      .catch(value => { if (!controller.signal.aborted) setError(value instanceof Error ? value.message : '无法获取服务端修改范围。'); })
      .finally(() => { if (!controller.signal.aborted) setScopePending(false); });
    return () => controller.abort();
  }, [session.id, session.version, selectedId, editorMode, fieldKey]);
  useEffect(() => () => { activeRequest.current?.abort(); }, []);

  async function generate() {
    if (!editorMode || !selectedId || !session.tripDocument) { setError('请先开启编辑模式，选择要修改的日期、时段或景点卡片。'); return; }
    if (!instruction.trim()) { setError('请先描述本次修改要求。'); return; }
    if (!scope?.allowedPaths.length || scope.locked || !fields.length) { setError(scope?.blockedReason || '当前选区没有可修改字段，请选择包含活动的未锁定区域。'); return; }
    if (hasDrafts) { setError('请先保存左侧尚未提交的输入，再生成候选。'); return; }
    discard();
    const epoch = generation.current;
    const controller = new AbortController(); activeRequest.current = controller;
    setPending(true); setProgress([]);
    try {
      const next = await api.editCandidates(session.id, session.tripDocument, [selectedId], instruction.trim(), agentMode, fields, controller.signal, session, (name,value) => {
        if(epoch!==generation.current || controller.signal.aborted)return;
        const labels:Record<string,string>={'scope.started':'正在解析选区','scope.validated':'服务端范围已确认','generation.started':'正在生成候选','generation.finished':'生成结束，开始检查','generation.retry':'合法候选不足，正在原范围补充一次','validation.started':'正在校验范围、锁与真实差异','validation.finished':'硬约束校验完成','candidate.validated':'候选已通过校验','candidates.complete':'候选与推荐排序已到达'};
        let text=labels[name]||name;
        if(name==='candidate.validated'&&value&&typeof value==='object'&&'strategy' in value&&'rank' in value){const strategy=String(value.strategy);text=`${STRATEGIES[strategy as CandidateStrategy]?.label||'候选'}已通过校验 · 推荐${String(value.rank)}`;}
        setProgress(items=>[...items.slice(-7),text]);
      });
      if (controller.signal.aborted || epoch !== generation.current) return;
      setBatch(next); setCandidateId(next.candidates[0]?.candidateId ?? '');
      setNotice('候选已校验。切换预览不改正式数据；点击“采用此方案”才保存。');
    } catch (value) {
      if (controller.signal.aborted || epoch !== generation.current) return;
      setError(`${binding?.label || '当前选区'} · 基于 v${session.version}：${value instanceof Error ? value.message : '生成失败，正式行程保持不变。'}`);
      if (value && typeof value === 'object' && 'status' in value && value.status === 409) onConflict(value);
    } finally { if (epoch === generation.current) setPending(false); }
  }
  async function commit() {
    if (!candidate || !batch || hasDrafts) return;
    if (expired || batch.baseVersion !== session.version) { setError('候选已过期或版本变化，请重新生成。'); return; }
    const epoch = generation.current;
    const ok = await onCommit(candidate.candidateId, batch.baseVersion);
    if (ok) { discard('已保存所选方案，其他候选未被采用。'); }
    else if (epoch === generation.current) setError(`${binding?.label || '当前选区'}的候选基于 v${batch.baseVersion}，本次未保存。正式行程未被候选覆盖，请根据版本提示重新生成或重试。`);
  }
  function differenceValue(field: keyof typeof FIELD_LABELS, value: string) {
    if (!value) return '（空）';
    if (field === 'placeId') return catalog.places.find(place => place.id === value)?.name || value;
    if (field === 'period') return PERIOD_LABELS[value as keyof typeof PERIOD_LABELS] || value;
    if (field === 'parentId') return PERIOD_LABELS[value.split('-').pop() as keyof typeof PERIOD_LABELS] ? PERIOD_LABELS[value.split('-').pop() as keyof typeof PERIOD_LABELS] + '分组' : value;
    return value;
  }
  function chooseFields(field: EditorField, checked: boolean) {
    discard(); setFields(current => checked ? [...current, field] : current.filter(item => item !== field));
  }
  return <section className="phase2-editor" data-testid="phase2-editor" aria-label="自然语言多策略候选编辑">
    <div className="control-panel-heading"><span className="assistant-icon"><Sparkles size={20} /></span><div><span className="panel-eyebrow">MAKE IT YOURS</span><h2>行程，听你的</h2></div><span className="assistant-status"><span className="status-dot" />{agentMode === 'mock' ? '规则演示' : '真实模型'}</span></div>
    <p className="control-intro">先选择局部，再描述需求。对比三个策略的真实效果，选中满意的方案后保存。</p>
    <div className="control-form">
      <label className="field-label" htmlFor="candidate-agent-mode">候选生成方式</label>
      <div className="select-wrap"><select id="candidate-agent-mode" value={agentMode} disabled={busy} onChange={event => { choseMode.current = true; discard(); setAgentMode(event.target.value as CandidateAgentMode); }}><option value="mock">Mock · 确定性规则演示（不调用模型）</option><option value="llm">LLM · 真实模型（需要服务端配置）</option></select><ChevronDown size={14} /></div>
      <p className="phase2-mode-hint">{agentMode === 'mock' ? '规则演示会依据当前行程调整描述和时点，不代表模型理解所有需求。' : status?.llmConfigured ? `由 ${status.provider || '配置的服务商'} / ${status.model} 生成；输出仍受相同权限校验。` : '模型尚未配置：维护者需在服务端 .env 设置 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL 后重启。不会自动改用 Mock。'}</p>
      <div className="phase2-scope" data-testid="phase2-effective-scope"><strong>{editorMode && binding ? binding.label : '尚未选择组件'}</strong><span>权威版本 v{session.version}</span><p>{scopePending ? '正在读取服务端范围…' : scope?.allowedPaths.length ? `${scope.eligibleActivityIds.length} 个景点 · ${fields.map(field => FIELD_LABELS[field]).join('、')}` : scope?.blockedReason || '请在编辑模式选择日期、时段或景点卡片；空时段无活动可改。'}</p>{scope?.locked && <p>选区已锁定，请先解除对应锁定。</p>}</div>
      <fieldset className="phase2-fields"><legend>本次允许修改的字段</legend>{(['description', 'startTime', 'placeId'] as const).map(field => <label key={field}><input type="checkbox" checked={fields.includes(field)} disabled={busy || pending} onChange={event => chooseFields(field, event.target.checked)} />{FIELD_LABELS[field]}</label>)}</fieldset>
      <label className="field-label" htmlFor="instruction">告诉我你的想法</label>
      <textarea id="instruction" aria-label="描述本次修改" className="instruction-input" rows={4} maxLength={2000} placeholder="例如：安排轻松一些，保留其他日期。" value={instruction} disabled={busy} onChange={event => { discard(); setInstruction(event.target.value); }} />
      <div className="input-caption"><span>默认生成 A / B / C 三个策略候选</span><span>{instruction.length}/2000</span></div>
      <div className="example-chips"><span className="example-label">试试这个需求</span><button type="button" disabled={busy} onClick={() => { discard(); setInstruction('安排轻松一些'); }}>安排轻松一些</button><button type="button" data-testid="example-replace-place" disabled={busy} onClick={() => { discard(); setFields(current => current.includes('placeId') ? current : [...current, 'placeId']); setInstruction('换一个真实景点，保留时间和其他内容'); }}>更换真实景点 · 同时勾选地点字段</button></div>
      <p className="phase2-mode-hint">可修改任意日期与时段中已选活动的描述、开始时间或真实景点。仅勾选本次允许更改的字段；名称和时段分组由系统自动同步。预算、锁定和选区外内容继续保护。</p>
      {hasDrafts && <p className="inline-warning">左侧有尚未保存的输入，请先保存。</p>}
      <button type="button" className="button primary generate-button" data-testid="generate-edit-candidates" disabled={busy || pending || scopePending || hasDrafts} onClick={() => void generate()}>{pending ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}{pending ? '正在生成并校验候选…' : '生成三个候选方案'}</button>
      {!!progress.length && <ol className="stream-progress" data-testid="generation-progress" aria-live="polite">{progress.map((step,index)=><li key={index}>{step}</li>)}</ol>}
      {pending && <div className="agent-progress" role="status"><span>正在生成受限 Patch；正式行程不会自动修改。</span><button type="button" onClick={() => discard('已停止等待，正式行程保持不变。')}>停止等待</button></div>}
    </div>
    {error && <div className="phase2-error" role="alert" data-testid="phase2-error"><p>{error}</p><button type="button" className="button secondary" disabled={busy||pending} onClick={()=>void generate()}>重试生成</button>{!fields.includes('placeId') && /景点|地点|placeId/.test(error) && <button type="button" className="button secondary" data-testid="authorize-place-field" disabled={busy || pending} onClick={() => { discard('已明确勾选真实景点字段，请再次生成候选。'); setFields(current => [...current, 'placeId']); }}>允许更换真实景点</button>}</div>}
    {notice && <p className="editor-status" role="status">{notice}</p>}
    {batch && candidate ? <section className="phase2-candidates" aria-label="候选比较" data-testid="phase2-candidates">
      <div className="phase2-batch-heading"><strong>{batch.candidateCount} / {batch.requestedCount} 个合格候选</strong><span>{batch.generationMode === 'mock' ? 'Mock · 未调用模型' : 'LLM · 真实模型'} · 基于 v{batch.baseVersion}</span></div>
      <div className="phase2-options" role="group" aria-label="切换候选方案">{batch.candidates.map(item => <button type="button" key={item.candidateId} data-testid={'phase2-candidate-' + STRATEGIES[item.strategy].letter} disabled={busy} aria-pressed={candidate.candidateId === item.candidateId} onClick={() => setCandidateId(item.candidateId)}><b>{STRATEGIES[item.strategy].letter}</b><span><strong>{STRATEGIES[item.strategy].label}</strong><small>{item.changedPaths.length} 个字段变化{item.evaluation ? ` · 推荐${item.evaluation.rank}` : ''}</small></span><Check size={13} /></button>)}</div>
      <h3>{candidate.label}</h3>{candidate.evaluation && <section className="candidate-evaluation" data-testid="candidate-evaluation"><strong>推荐第 {candidate.evaluation.rank} · 规则综合分 {candidate.evaluation.score.toFixed(3)}</strong><p>硬约束优先；评分是规则代理，未经用户实验验证。</p><details><summary>查看五项指标与评分依据</summary><ul>{Object.entries(candidate.evaluation.metrics).map(([key,value])=><li key={key}>{({semanticFit:'语义匹配代理',functionalFit:'结构功能校验',stability:'授权稳定性',usability:'可用性启发式',changeEconomy:'少改动程度'} as Record<string,string>)[key]||key}：{value.toFixed(3)}</li>)}</ul>{candidate.evaluation.evidence.map((text,index)=><p key={index}>{text}</p>)}</details></section>}<p className="phase2-rationale">{candidate.rationale}</p>
      <div className="phase2-validation" data-testid="phase2-validation"><ShieldCheck size={14} /><span>服务端通过：结构、范围、锁定、时间顺序、实际叶子差异与原业务差异。</span></div>
      <CandidatePreview candidate={candidate} batch={batch} session={session} catalog={catalog} />
      <details className="phase2-differences" open><summary>查看具体差异 · {candidate.differences.length} 项</summary><ul>{candidate.differences.map(item => <li key={item.path}><strong>{item.title} · {FIELD_LABELS[item.field]}</strong><del>{differenceValue(item.field, item.before)}</del><ins>{differenceValue(item.field, item.after)}</ins>{candidate.derivedChangedPaths.includes(item.path) && <small>由地点目录或开始时间自动同步，不是额外写入权限</small>}<code>{item.path}</code></li>)}</ul></details>
      {!!batch.rejectedCandidates.length && <div className="phase2-rejections"><strong>以下策略未纳入候选</strong>{batch.rejectedCandidates.map((item, index) => <p key={index}>{STRATEGIES[item.strategy as CandidateStrategy]?.label || item.strategy}：{item.message}</p>)}</div>}
      {batch.diagnostics.map((text, index) => <p className="phase2-mode-hint" key={index}>{text}</p>)}
      <p className="phase2-expiry">候选有效至 {new Date(candidate.expiresAt).toLocaleTimeString('zh-CN', { hour12: false })}；页面版本变化后需重新生成。</p>
      {expired && <p className="phase2-error" role="alert">此候选已过期，请点击“生成三个候选方案”重新生成，正式行程保持不变。</p>}
      <div className="proposal-actions"><button type="button" className="button primary" data-testid="commit-edit-candidate" disabled={busy || pending || hasDrafts || expired || batch.baseVersion !== session.version} onClick={() => void commit()}><Check size={15} />采用此方案 · {STRATEGIES[candidate.strategy].letter}</button><button type="button" className="button ghost" data-testid="cancel-edit-candidates" disabled={busy} onClick={() => discard('已取消全部候选，正式行程和锁定保持不变。')}><X size={15} />取消</button></div>
    </section> : <div className="preview-placeholder"><span className="preview-illustration"><Sparkles size={22} /></span><strong>比较之后，再做决定</strong><p>A 最小改动 · B 效率优先 · C 体验优先<br />只有合法且存在实际差异的方案才会展示。</p></div>}
  </section>;
}
