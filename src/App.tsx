import { Fragment, useEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowDownToLine, ArrowLeft, ArrowRight, ArrowUpRight, BookOpen, Bus, CalendarDays, Check, CheckCircle2, ChevronDown, Clock3, Compass, History, LayoutGrid, Leaf, LoaderCircle, LockKeyhole, MapPin, Mountain, Plus, RefreshCw, RotateCcw, Settings2, ShieldCheck, Sparkles, Sun, Umbrella, UnlockKeyhole, Wallet, X } from 'lucide-react';
import { api, ApiError } from './api';
import { DayRouteMap, GeneratedBlocks, TransportLeg } from './components/JourneyComponents';
import { LocalEditor, bindingFor, isComponentLocked, PERIOD_LABELS, selectionAttributes } from './features/agentic-editor/LocalEditor';
import { TripSetup } from './components/TripSetup';
import { GuidePage } from './components/GuidePage';
import { ServiceSettings } from './components/ServiceSettings';
import { StopCardContent } from './components/StopCardContent';
import { CandidatePanel } from './features/agentic-editor/CandidatePanel';
import { VersionHistory } from './features/agentic-editor/VersionHistory';
import { GlobalControls } from './features/agentic-editor/GlobalControls';
import type { DisplayChange } from './features/agentic-editor/historyTypes';
import { sessionDayItems, type TripSetupRequest, type AgentStatus, type Catalog, type DayKey, type DirectUpdate, type IntegrationStatus, type Proposal, type ProposalBatch, type RegionId, type Session, type TargetRegion } from './types';

const STORAGE_KEY = 'travel-genui-phase1-session-v1';
const money = (n: number) => `¥${n.toLocaleString('zh-CN')}`;
const officialLink = (value?: string) => {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : undefined;
  } catch { return undefined; }
};
function revealInPane(target: HTMLElement | null) {
  if (!target) return;
  const pane = target.closest<HTMLElement>('.itinerary-column, .control-panel');
  const behavior = matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
  if (pane && ['auto', 'scroll'].includes(getComputedStyle(pane).overflowY)) {
    const top = target.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop - pane.clientTop - 18;
    pane.scrollTo({ top: Math.max(0, top), behavior });
    return;
  }
  target.scrollIntoView({ behavior, block: 'start' });
}
type Toast = { id: number; message: string; kind: 'success' | 'error' | 'info' };

function Overlay({ children, onClose, className, label }: { children: ReactNode; onClose: () => void; className: string; label: string }) {
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const getFocusable = () => [...(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), [href], input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex="0"]') ?? [])];
    getFocusable()[0]?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      if (event.key !== 'Tab') return;
      const items = getFocusable();
      if (!items.length) return;
      if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items[items.length - 1]?.focus(); }
      else if (!event.shiftKey && document.activeElement === items[items.length - 1]) { event.preventDefault(); items[0]?.focus(); }
    };
    document.addEventListener('keydown', onKeyDown);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.removeEventListener('keydown', onKeyDown); document.body.style.overflow = overflow; previous?.focus(); };
  }, [onClose]);
  return <div className={className === 'history-drawer' ? 'drawer-backdrop' : 'dialog-backdrop'} onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}><section ref={panel} role="dialog" aria-modal="true" aria-label={label} className={className}>{children}</section></div>;
}

function ScenicIllustration() {
  return <svg viewBox="0 0 400 205" fill="none" aria-hidden="true" className="scenic-illustration"><circle cx="310" cy="49" r="27" fill="#E9C899"/><path d="M55 164 157 45 242 164" fill="#CCD8BF"/><path d="m127 83 30-38 29 40-29-13-14 17-16-6Z" fill="#F6F4EA"/><path d="m164 167 96-102 90 102" fill="#A3B69A"/><path d="m238 89 22-24 27 30-27-11-10 10-12-5Z" fill="#EDF0E4"/><path d="M15 158c74-26 118-9 164 5 67 20 141-16 212-8v39H15v-36Z" fill="#D6DEC8"/><path d="M40 185c78-22 172 11 245-9 40-11 73-5 99 0" stroke="#93B3A1" strokeWidth="3"/><path d="M76 169v-39m-16 23 16-30 16 30H60Zm5-15 11-25 11 25H65Z" fill="#51745D"/><path d="M339 166v-43m-18 25 18-34 18 34h-36Zm6-18 12-27 12 27h-24Z" fill="#56795E"/><path d="M116 177c55-12 113 16 181-3" stroke="#F3E7CC" strokeWidth="7" strokeLinecap="round"/><path d="M206 153h44v23h-44v-23Z" fill="#F5EADB"/><path d="m200 154 28-21 29 21h-57Z" fill="#9D6750"/><path d="M224 163h8v13h-8v-13Z" fill="#6C8771"/><path d="M37 57c5-5 9-5 14 0m2-9c5-5 9-5 14 0" stroke="#71917C" strokeWidth="2" strokeLinecap="round"/><path d="M93 96c5-5 9-5 14 0" stroke="#71917C" strokeWidth="2" strokeLinecap="round"/></svg>;
}

export default function App() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const dayItems = sessionDayItems(session);
  const [showSetup, setShowSetup] = useState(true);
  const [activePage, setActivePage] = useState<'planner' | 'guide'>('planner');
  const [tripOverviewCollapsed, setTripOverviewCollapsed] = useState(false);
  const [setupError, setSetupError] = useState('');
  const [agentStatus, setAgentStatus] = useState<AgentStatus | null>(null);
  const [generatingSeconds, setGeneratingSeconds] = useState(0);
  const sessionRef = useRef<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [connectionError, setConnectionError] = useState('');
  const [reloadKey, setReloadKey] = useState(0);
  const [editorMode, setEditorMode] = useState(false);
  const [selectedComponentId, setSelectedComponentId] = useState<string | null>(null);
  const [selectedDay, setSelectedDay] = useState<DayKey>('day1');
  const [targetRegion, setTargetRegion] = useState<TargetRegion>('day-1');
  const [instruction, setInstruction] = useState('');
  const [generateCandidates, setGenerateCandidates] = useState(false);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [proposalBatch, setProposalBatch] = useState<ProposalBatch | null>(null);
  const [showCandidatePreview, setShowCandidatePreview] = useState(true);
  const [surfaceErrors, setSurfaceErrors] = useState<Record<string, string>>({});
  const repairAttempts = useRef(0);
  const [surfaceReady, setSurfaceReady] = useState<Record<string, boolean>>({});
  const stateSaveQueue = useRef<Promise<void>>(Promise.resolve());
  const pendingStateSaves = useRef(0);
  const previewReturnDay = useRef<DayKey>('day1');
  const previewSurface = useRef<HTMLDivElement>(null);
  const proposalPanel = useRef<HTMLElement>(null);
  const [proposalRevision, setProposalRevision] = useState(0);
  const [editRevision, setEditRevision] = useState(0);
  const revisionRef = useRef(0);
  const [busy, setBusy] = useState('');
  const busyRef = useRef(false);
  const actionController = useRef<AbortController | null>(null);
  const [noteDrafts, setNoteDrafts] = useState<Record<string, string>>({});
  const [timeDrafts, setTimeDrafts] = useState<Record<string, string>>({});
  const [budgetDraft, setBudgetDraft] = useState('');
  const [replaceStopId, setReplaceStopId] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [serviceSettingsOpen, setServiceSettingsOpen] = useState(false);
  const [serviceSettingsTarget, setServiceSettingsTarget] = useState<'model' | 'amap'>('model');
  const [serviceRevision, setServiceRevision] = useState(0);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toastId = useRef(0);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  function notify(message: string, kind: Toast['kind'] = 'success') {
    const id = ++toastId.current;
    setToasts(items => [...items.slice(-2), { id, message, kind }]);
    timers.current.push(setTimeout(() => setToasts(items => items.filter(item => item.id !== id)), 5200));
  }
  function markEdited() {
    const next = ++revisionRef.current;
    setEditRevision(next);
    return next;
  }
  function commitSession(next: Session, forceDrafts = false) {
    const previous = sessionRef.current;
    if (previous?.id !== next.id) { setSelectedComponentId(null); setEditorMode(false); }
    sessionRef.current = next;
    setSession(next);
    if (next.catalog) setCatalog(next.catalog);
    const firstDay = sessionDayItems(next)[0];
    if (firstDay) {
      setSelectedDay(current => next.tripState.days[current] ? current : firstDay.key);
      setTargetRegion(current => current === 'all' || next.uiSpec.regions[current] ? current : firstDay.region);
    }
    setNoteDrafts(drafts => {
      const result: Record<string, string> = forceDrafts ? {} : { ...drafts };
      sessionDayItems(next).forEach(({ key }) => { if (forceDrafts || drafts[key] === undefined || drafts[key] === previous?.tripState.days[key]?.note) result[key] = next.tripState.days[key].note; });
      return result;
    });
    setTimeDrafts(drafts => {
      const result: Record<string, string> = forceDrafts ? {} : { ...drafts };
      const oldStops = Object.values(previous?.tripState.days ?? {}).flatMap(day => day.stops);
      const allStops = Object.values(next.tripState.days).flatMap(day => day.stops);
      allStops.forEach(stop => { if (forceDrafts || drafts[stop.id] === undefined || drafts[stop.id] === oldStops.find(old => old.id === stop.id)?.startTime) result[stop.id] = stop.startTime; });
      return result;
    });
    setBudgetDraft(draft => forceDrafts || !previous || draft === String(previous.tripState.budgetLimit) ? String(next.tripState.budgetLimit) : draft);
    try { localStorage.setItem(STORAGE_KEY, next.id); } catch { /* An in-memory session still works if storage is unavailable. */ }
  }

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setConnectionError('');
    void (async () => {
      try {
        const status = await api.agentStatus(controller.signal).catch(() => null);
        let savedId: string | null = null;
        try { savedId = localStorage.getItem(STORAGE_KEY); } catch { /* Private browsing can disable local storage. */ }
        let nextSession: Session | null = null;
        if (savedId) {
          try { nextSession = await api.session(savedId, controller.signal); }
          catch (error) { if (!(error instanceof ApiError && error.status === 404)) throw error; }
        }
        if (controller.signal.aborted) return;
        setAgentStatus(status);
        const nextCatalog = nextSession?.catalog ?? await api.catalog(controller.signal);
        if (controller.signal.aborted) return;
        setCatalog(nextCatalog);
        if (nextSession) commitSession(nextSession, true);
        setShowSetup(true);
      } catch (error) {
        if (!controller.signal.aborted) setConnectionError(error instanceof Error ? error.message : '无法载入行程，请重试。');
      } finally { if (!controller.signal.aborted) setLoading(false); }
    })();
    return () => controller.abort();
  }, [reloadKey]);
  useEffect(() => () => { actionController.current?.abort(); timers.current.forEach(clearTimeout); }, []);
  useEffect(() => {
    if (loading || connectionError || showSetup || !session) {
      setTripOverviewCollapsed(false);
      return;
    }
    setTripOverviewCollapsed(false);
    const timer = setTimeout(() => setTripOverviewCollapsed(true), 1400);
    return () => clearTimeout(timer);
  }, [connectionError, loading, session?.id, showSetup]);
  useEffect(() => {
    if (editorMode) setTripOverviewCollapsed(true);
  }, [editorMode]);
  useEffect(() => {
    if (busy !== '正在生成预览') { setGeneratingSeconds(0); return; }
    const startedAt = Date.now();
    const timer = setInterval(() => setGeneratingSeconds(Math.floor((Date.now() - startedAt) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [busy]);
  useEffect(() => {
    const stops = session?.tripState.days[selectedDay]?.stops ?? [];
    setReplaceStopId(current => stops.some(stop => stop.id === current) ? current : stops[stops.length - 1]?.id ?? '');
  }, [selectedDay, session]);

  async function planTrip(settings: TripSetupRequest) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy('正在规划行程');
    setSetupError('');
    const controller = new AbortController();
    actionController.current = controller;
    try {
      const next = await api.createSession(settings, controller.signal);
      if (controller.signal.aborted) return;
      if (!next.catalog?.places.length || !next.catalog.hotels.length || !sessionDayItems(next).length) throw new ApiError(502, '规划结果缺少完整行程或地点目录，请重试。');
      const first = sessionDayItems(next)[0];
      commitSession(next, true);
      setSelectedDay(first.key); setTargetRegion(first.region); previewReturnDay.current = first.key;
      setProposal(null); setProposalBatch(null); setSurfaceErrors({}); setSurfaceReady({}); repairAttempts.current = 0;
      setInstruction(''); setGenerateCandidates(false); setShowCandidatePreview(true);
      markEdited(); setActivePage('planner'); setShowSetup(false);
      notify('行程已规划并保存，可以继续用文字调整页面。');
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'AbortError')) setSetupError(error instanceof Error ? error.message : '规划失败，请稍后重试。');
    } finally { busyRef.current = false; setBusy(''); }
  }
  async function openDemoTrip(settings: TripSetupRequest) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy('正在规划行程'); setSetupError('');
    const controller = new AbortController(); actionController.current = controller;
    try {
      const next = await api.createDemoSession(settings, controller.signal);
      if (controller.signal.aborted) return;
      commitSession(next, true);
      const first = sessionDayItems(next)[0];
      setSelectedDay(first.key); setTargetRegion(first.region);
      setSelectedComponentId(null); setEditorMode(false);
      setProposal(null); setProposalBatch(null); markEdited(); setActivePage('planner'); setShowSetup(false);
      notify('已按当前日期、天数、预算与偏好生成杭州示例；未调用模型或地图服务。', 'info');
    } catch (error) { setSetupError(error instanceof Error ? error.message : '演示行程载入失败。'); }
    finally { busyRef.current = false; setBusy(''); }
  }
  function selectEditorComponent(id: string) {
    const binding = sessionRef.current?.componentRegistry?.[id];
    if (!binding || busyRef.current) return;
    setSelectedComponentId(id); setEditorMode(true);
    setSelectedDay(binding.dayKey); setTargetRegion(binding.regionId);
    requestAnimationFrame(() => revealInPane(document.querySelector('.local-editor-panel')));
  }
  function setEditingMode(enabled: boolean) {
    setEditorMode(enabled);
    if (!enabled) setSelectedComponentId(null);
    else requestAnimationFrame(() => revealInPane(document.querySelector('.local-editor-panel')));
    if (proposal) cancelProposal();
  }
  function lockEditorComponent(componentId: string, locked: boolean) {
    if (hasDrafts) { notify('请先保存现有输入，再修改组件锁定。', 'info'); return; }
    markEdited();
    void mutate('正在保存组件锁定', (current, signal) => api.editorLock(current.id, current.version, componentId, locked, signal), locked ? '组件锁定已保存。' : '组件锁定已解除。');
  }
  async function applyEditorPatch(previewId: string, baseVersion: number) {
    if (hasDrafts || baseVersion !== sessionRef.current?.version) { notify('页面已有变化，请保存输入并重新校验补丁。', 'info'); return false; }
    const ok = await mutate('正在应用局部修改', (current, signal) => api.editorApply(current.id, current.version, previewId, signal), '局部修改已保存，未选内容继续保留。', true);
    if (ok) markEdited();
    return ok;
  }
  async function applyEditCandidate(candidateId: string, baseVersion: number) {
    if (hasDrafts || baseVersion !== sessionRef.current?.version) { notify('行程已有变化，请先保存输入并重新生成候选。', 'info'); return false; }
    const ok = await mutate('正在采用所选方案', (current, signal) => api.commitEditCandidate(current.id, current.version, candidateId, signal), '已采用所选方案，其余候选未写入，未授权内容保持不变。', true);
    if (ok) markEdited();
    return ok;
  }
  async function applyEditorDisplay(componentId: string, change: DisplayChange) {
    if (hasDrafts) { notify('请先保存输入，再修改整日展示。', 'info'); return false; }
    markEdited();
    return mutate('正在保存局部展示', (current, signal) => api.editorDisplay(current.id, current.version, componentId, change, signal), '已保存所选日期的展示设置；行程业务数据保持。');
  }
  async function applyGlobalPresentation(show: boolean, baseVersion: number) {
    if (hasDrafts || baseVersion !== sessionRef.current?.version) { notify(`授权基于 v${baseVersion}，当前为 v${sessionRef.current?.version ?? '?'}；请保存输入并重新确认。`, 'info'); return false; }
    markEdited();
    return mutate('正在保存全局展示', (current, signal) => api.globalPresentation(current.id, baseVersion, show, signal), '全局预算展示已按明确授权保存。');
  }
  async function applyBudgetConstraint(maxBudget: number | null, baseVersion: number) {
    if (hasDrafts || baseVersion !== sessionRef.current?.version) { notify(`约束授权基于 v${baseVersion}，当前版本已变化；请重新确认。`, 'info'); return false; }
    markEdited();
    return mutate('正在保存预算硬约束', (current, signal) => api.budgetConstraint(current.id, baseVersion, maxBudget, signal), '预算输入硬约束已按明确授权保存。');
  }
  async function restoreSavedVersion(targetVersion: number, baseVersion: number) {
    if (hasDrafts || baseVersion !== sessionRef.current?.version) { notify(`恢复确认基于 v${baseVersion}，当前为 v${sessionRef.current?.version ?? '?'}；请保存输入并重新确认。`, 'info'); return false; }
    markEdited();
    const ok = await mutate('正在恢复保存快照', (current, signal) => api.restoreVersion(current.id, baseVersion, targetVersion, signal), `已恢复 v${targetVersion} 的行程、展示及约束，并创建新版本。`, true);
    if (ok) { setProposal(null); setProposalBatch(null); setSelectedComponentId(null); setEditorMode(false); }
    return ok;
  }
  function openTripSetup() {
    if (busyRef.current) return false;
    if (hasDrafts || pendingStateSaves.current) { notify('请先等待已有输入保存，再规划新行程。', 'info'); return false; }
    setSetupError(''); setActivePage('planner'); setShowSetup(true); setProposal(null); setProposalBatch(null); setHistoryOpen(false); setResetOpen(false);
    markEdited();
    return true;
  }
  async function refreshAgentStatus() {
    try {
      const status = await api.agentStatus();
      setAgentStatus(status);
      notify(status.configured || status.mode === 'demo' ? '已更新后台生成服务状态。' : '后台模型仍未配置，现有行程可以继续手动编辑。', 'info');
    } catch (error) { notify(error instanceof Error ? error.message : '暂时无法检查后台服务。', 'error'); }
  }

  function openServiceSettings(target: 'model' | 'amap' = 'model') {
    setServiceSettingsTarget(target);
    setServiceSettingsOpen(true);
  }

  async function integrationChanged(_status: IntegrationStatus) {
    const next = await api.agentStatus().catch(() => null);
    if (next) setAgentStatus(next);
    setServiceRevision(value => value + 1);
  }

  async function recoverError(error: unknown) {
    if (error instanceof DOMException && error.name === 'AbortError') return;
    if (error instanceof ApiError && error.status === 409 && sessionRef.current) {
      try {
        const fresh = await api.session(sessionRef.current.id, actionController.current?.signal);
        commitSession(fresh);
        markEdited();
        notify(`${error.message} 已同步最新状态，请确认后重新操作。`, 'error');
      } catch { notify('同步最新状态失败，请刷新页面后重试。', 'error'); }
    } else notify(error instanceof Error ? error.message : '操作失败，请重试。', 'error');
  }
  async function mutate(label: string, action: (current: Session, signal: AbortSignal) => Promise<Session>, success?: string, forceDrafts = false) {
    if (busyRef.current || !sessionRef.current) return false;
    busyRef.current = true;
    setBusy(label);
    const controller = new AbortController();
    actionController.current = controller;
    try {
      const next = await action(sessionRef.current, controller.signal);
      if (controller.signal.aborted) return false;
      commitSession(next, forceDrafts);
      if (success) notify(success);
      return true;
    } catch (error) { await recoverError(error); return false; }
    finally { busyRef.current = false; setBusy(''); }
  }
  function direct(update: DirectUpdate, success: string) {
    if (busyRef.current) { notify('上一项操作正在保存，请稍后再试。', 'info'); return; }
    markEdited();
    void mutate('正在保存', (current, signal) => api.direct(current.id, current.version, update, signal), success);
  }
  const hasDrafts = !!session && (dayItems.some(({ key }) => noteDrafts[key] !== undefined && noteDrafts[key] !== session.tripState.days[key].note)
    || Object.values(session.tripState.days).some(day => day.stops.some(stop => timeDrafts[stop.id] !== undefined && timeDrafts[stop.id] !== stop.startTime))
    || (budgetDraft !== String(session.tripState.budgetLimit)));

  async function preview(_text = instruction, _target = targetRegion, _revision = revisionRef.current, _withCandidates = generateCandidates) {
    notify('旧自由代码生成已停用。请在自然语言候选面板选择要修改的日期、时段或景点，生成并比较结构化方案。', 'info');
  }
  async function repairSurface(message: string, auto = false) {
    const current = sessionRef.current;
    if (!current || !proposal || busyRef.current || staleProposal) return;
    if (auto && repairAttempts.current >= 1) return;
    repairAttempts.current += 1;
    busyRef.current = true;
    setBusy('正在修复生成界面');
    const controller = new AbortController();
    actionController.current = controller;
    try {
      const result = await api.repair(current.id, current.version, proposal.proposalId, message, controller.signal);
      if (controller.signal.aborted) return;
      const candidates = result.candidates?.length ? result.candidates : [result];
      const withCandidates = proposalBatch?.generateCandidates ?? generateCandidates;
      if ((withCandidates && candidates.length < 3) || (!withCandidates && candidates.length !== 1)) {
        throw new ApiError(502, '修复结果未满足所选方案数量，请重新预览。');
      }
      setProposalBatch({ ...result, generateCandidates: withCandidates, candidates });
      setProposal(candidates[0]);
      setSurfaceErrors({});
      setShowCandidatePreview(true);
      notify('Agent已根据浏览器错误修复候选，请检查新界面。', 'info');
    } catch (error) { await recoverError(error); }
    finally { busyRef.current = false; setBusy(''); }
  }
  function surfaceFailed(message: string) {
    if (!proposal || staleProposal) { notify('生成界面运行失败：' + message + '。可向Agent描述问题并要求修复。', 'error'); return; }
    setSurfaceErrors(previous => ({ ...previous, [proposal.proposalId]: message }));
    // One automatic repair per original request, with the same frozen scope.
    if (repairAttempts.current === 0) void repairSurface(message, true);
  }
  async function saveSurfaceState(value: Record<string, unknown>) {
    if (previewing || hasDrafts) throw new Error('请等待已有输入保存后再操作。');
    const region = activeRegion;
    const sessionId = sessionRef.current?.id;
    pendingStateSaves.current += 1;
    const save = stateSaveQueue.current.catch(() => {}).then(async () => {
      const started = Date.now();
      while (busyRef.current) {
        if (Date.now() - started > 30000) throw new Error('交互状态尚未保存，请稍后重试。');
        await new Promise(resolve => setTimeout(resolve, 60));
      }
      if (sessionRef.current?.id !== sessionId || !sessionRef.current?.uiSpec.regions[region].surface) throw new Error('交互界面已变化，请重试。');
      markEdited();
      const ok = await mutate('正在保存交互状态', (current, signal) => api.direct(current.id, current.version, { kind: 'surface_state', targetRegionId: region, value }, signal));
      if (!ok) throw new Error('交互状态未保存，请重试。');
    });
    stateSaveQueue.current = save;
    try { await save; } finally { pendingStateSaves.current -= 1; }
  }
  function changeDay(key: DayKey, region: RegionId) {
    setSelectedDay(key);
    if (editorMode) setSelectedComponentId(bindingFor(sessionRef.current, key)?.componentId ?? null);
    // Browsing a candidate across dates is not an edit or a new generation target.
    if (proposal && !staleProposal) return;
    setTargetRegion(region);
    markEdited();
  }
  function selectCandidate(candidate: Proposal) {
    if (busyRef.current || staleProposal) return;
    setProposal(candidate);
    setShowCandidatePreview(true);
  }
  function cancelProposal() {
    setProposal(null);
    setProposalBatch(null);
    setShowCandidatePreview(true);
    setSelectedDay(previewReturnDay.current);
  }
  function fillInstruction(text: string) { setInstruction(text); markEdited(); }
  function changeCandidateMode(checked: boolean) {
    if (busyRef.current || checked === generateCandidates) return;
    setGenerateCandidates(checked);
    markEdited();
    if (proposal) cancelProposal();
    setSurfaceErrors({});
    setSurfaceReady({});
    repairAttempts.current = 0;
  }
  function previewView(view: 'cards' | 'timeline') {
    if (hasDrafts || busyRef.current || !sessionRef.current) return;
    markEdited();
    void mutate('正在切换展示方式', (current, signal) => api.presentation(current.id, current.version, activeRegion, view, signal), '展示方式已更新。');
  }
  async function applyProposal() {
    if (!proposal || !session || proposal.baseVersion !== session.version || proposalRevision !== revisionRef.current || hasDrafts) { notify('需求或页面已变化，请重新预览后再应用。', 'info'); return; }
    if (surfaceErrors[proposal.proposalId]) { notify('生成界面存在运行错误，请先修复或选择其他候选。', 'error'); return; }
    if (!allSurfacesReady) { notify('正在检查各日期的生成界面，请稍候。', 'info'); return; }
    const names = (proposal.affectedRegionIds ?? [proposal.targetRegionId]).map(region => dayItems.find(item => item.region === region)?.label ?? '所选日期').join('、') || '行程设置';
    const ok = await mutate('正在应用修改', (current, signal) => api.apply(current.id, current.version, proposal.proposalId, signal), `已更新${names}，原有输入与未涉及的内容继续保留。`);
    if (ok) { setProposal(null); setProposalBatch(null); setShowCandidatePreview(true); setInstruction(''); markEdited(); }
  }
  async function refreshRoutes(target: TargetRegion = activeRegion) {
    if (busyRef.current || !sessionRef.current) return;
    if (!agentStatus?.mapConfigured) { notify('请先接入高德地图 API，再查询交通与地图。', 'info'); openServiceSettings('amap'); return; }
    if (hasDrafts) { notify('请先移开输入框保存备注、时间或预算，再查询路线。', 'info'); return; }
    markEdited();
    const ok = await mutate('正在查询路线', (current, signal) => api.refreshRoutes(current.id, current.version, target, signal));
    if (!ok) return;
    const regions = target === 'all' ? dayItems.map(item => item.region) : [target];
    const routes = regions.map(region => sessionRef.current?.routeData?.[region]);
    const unavailable = routes.find(route => !route || route.status !== 'ready');
    if (!unavailable && routes.every(route => route?.status === 'ready')) {
      notify(target === 'all' ? '全部日期的交通与地图已更新，原有景点和备注保留。' : '这一天的交通与地图已更新，原有景点和备注保留。');
    } else {
      notify(unavailable?.message || '已添加交通与地图展示，部分地点或路线仍需确认。', 'info');
    }
  }
  function toggleLock(target: RegionId | 'hotel', locked: boolean) {
    markEdited();
    void mutate('正在保存锁定', (current, signal) => api.lock(current.id, current.version, target, locked, signal), locked ? '已锁定，后续生成会遵守这项约束。' : '已解除锁定。');
  }
  function saveNote(key: DayKey, region: RegionId) {
    const value = noteDrafts[key];
    if (session && value !== undefined && value !== session.tripState.days[key].note) direct({ kind: 'note', targetRegionId: region, value }, '行程备注已保存。');
  }
  function saveTime(stopId: string) {
    const value = timeDrafts[stopId];
    const previous = sessionRef.current?.tripState.days[selectedDay]?.stops.find(stop => stop.id === stopId)?.startTime;
    if (!value && previous) { setTimeDrafts(drafts => ({ ...drafts, [stopId]: previous })); notify('请保留一个有效的开始时间。', 'info'); return; }
    if (value && value !== previous) direct({ kind: 'time', targetRegionId: activeRegion, stopId, value }, '出发时间已保存。');
  }
  function saveBudget() {
    if (!session || budgetDraft === String(session.tripState.budgetLimit)) return;
    const value = Number(budgetDraft);
    if (!budgetDraft.trim() || !Number.isFinite(value) || value < 100 || value > 100000) { notify('预算上限请填写 100–100,000 之间的金额。', 'error'); setBudgetDraft(String(session.tripState.budgetLimit)); return; }
    direct({ kind: 'budget', value }, '预算上限已更新。');
  }
  function exportPlan() {
    if (!session) return;
    const blob = new Blob([JSON.stringify({ exportedAt: new Date().toISOString(), demoNotice: '门票、住宿及其他费用为预算估算，不代表实时票价、房态或预订。地图查询结果单独记录来源与时间。', ...session }, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = `旅序-${session.tripState.city}-${dayItems.length}天行程.json`; link.click(); URL.revokeObjectURL(url);
    notify('行程 JSON 已导出。');
  }

  const dayItem = dayItems.find(item => item.key === selectedDay) ?? dayItems[0] ?? { key: 'day1' as DayKey, region: 'day-1' as RegionId, label: '第一天', number: '01' };
  const activeRegion = dayItem.region;
  const activeDayBinding = bindingFor(session, selectedDay);
  const selectedBinding = selectedComponentId ? session?.componentRegistry?.[selectedComponentId] : undefined;
  const dayComponentLocked = !!session && isComponentLocked(session, activeDayBinding?.componentId, false);
  const staleProposal = !!proposal && (proposal.baseVersion !== session?.version || proposalRevision !== editRevision || hasDrafts);
  const previewing = !!proposal && !staleProposal && showCandidatePreview;
  const candidateUpdate = previewing ? proposal.updates?.find(update => update.regionId === activeRegion)
    ?? (proposal.targetRegionId === activeRegion ? { regionId: activeRegion, nextUI: proposal.nextUI, nextData: proposal.nextData } : undefined) : undefined;
  const day = candidateUpdate?.nextData ?? session?.tripState.days[selectedDay];
  const ui = candidateUpdate?.nextUI ?? session?.uiSpec.regions[activeRegion];
  const regionLocked = !!session?.controlState.lockedRegions.includes(activeRegion);
  const targetLocked = targetRegion !== 'all' && !!session?.controlState.lockedRegions.includes(targetRegion);
  const hasLockedDays = !!session?.controlState.lockedRegions.length;
  const agentDemo = agentStatus?.mode === 'demo';
  const connectedServiceCount = Number(!!agentStatus?.configured) + Number(!!agentStatus?.mapConfigured);
  const serviceConnectionClass = !agentStatus ? 'is-pending' : connectedServiceCount === 2 ? 'is-complete' : connectedServiceCount === 1 ? 'is-partial' : 'is-empty';
  const serviceConnectionTitle = !agentStatus ? '正在读取服务状态' : `${agentStatus.configured ? `模型已接入${agentStatus.provider ? ` · ${agentStatus.provider}` : ''}` : '模型未接入'}；${agentStatus.mapConfigured ? '高德地图已接入' : '高德地图未接入'}`;
  const routeData = previewing ? candidateUpdate?.routeData ?? (candidateUpdate?.nextData ? undefined : session?.routeData?.[activeRegion]) : session?.routeData?.[activeRegion];
  const candidateMapId = previewing && candidateUpdate?.routeData ? proposal.proposalId : undefined;
  const candidateOptions = proposalBatch?.candidates ?? (proposal ? [proposal] : []);
  const scopeSummary = proposalBatch?.scopeSummary ?? [];
  const preservedSummary = proposalBatch?.preservedSummary ?? proposal?.preserved ?? [];
  const candidateNotes = proposalBatch?.candidateNotes ?? [];
  const routeMap = session && ui?.showMap ? <DayRouteMap sessionId={session.id} version={session.version} region={activeRegion} dayName={dayItem.label} route={routeData} proposalId={candidateMapId} /> : null;
  const proposalRegions = proposal?.affectedRegionIds ?? proposal?.updates?.map(update => update.regionId) ?? (proposal && proposal.targetRegionId !== 'all' ? [proposal.targetRegionId] : []);
  const displayTrip = session ? { ...session.tripState, ...(previewing ? proposal?.nextTrip ?? {} : {}) } : undefined;
  const selectedHotel = catalog?.hotels.find(hotel => hotel.id === displayTrip?.hotelId);
  const hotelGroups = catalog ? [
    { label: '预算友好 · ¥700 以下', hotels: catalog.hotels.filter(hotel => hotel.nightlyPrice <= 700) },
    { label: '舒适精选 · ¥701–¥1,200', hotels: catalog.hotels.filter(hotel => hotel.nightlyPrice > 700 && hotel.nightlyPrice <= 1200) },
    { label: '度假品质 · ¥1,200 以上', hotels: catalog.hotels.filter(hotel => hotel.nightlyPrice > 1200) },
  ].filter(group => group.hotels.length) : [];
  const placeById = (id: string) => catalog?.places.find(place => place.id === id);
  const alternatives = catalog?.places.filter(place => place.indoor && !day?.stops.some(stop => stop.placeId === place.id)) ?? [];
  const replaceStop = day?.stops.find(stop => stop.id === replaceStopId);
  const replaceName = replaceStop ? placeById(replaceStop.placeId)?.name ?? '当前景点' : '当前景点';
  const displayDerived = (() => {
    if (!session) return { attractions: 0, transport: 0, food: 0, hotel: 0, total: 0, remaining: 0 };
    if (!previewing || !catalog) return session.derived;
    const updates = proposal.updates ?? (proposal.targetRegionId !== 'all' ? [{ regionId: proposal.targetRegionId, nextUI: proposal.nextUI, nextData: proposal.nextData }] : []);
    const days = dayItems.map(item => updates.find(update => update.regionId === item.region)?.nextData ?? session.tripState.days[item.key]);
    const places = days.flatMap(item => item.stops).map(stop => placeById(stop.placeId));
    const travelers = displayTrip?.travelers ?? 1;
    const attractions = places.reduce((sum, place) => sum + (place?.cost ?? 0), 0) * travelers;
    const transport = places.reduce((sum, place) => sum + (place?.transportCost ?? 0), 0) * travelers;
    const food = (catalog.pricing?.foodPerPersonDay ?? 180) * days.length * travelers;
    const hotel = (selectedHotel?.nightlyPrice ?? 0) * (displayTrip?.nights ?? session.tripState.nights) * (displayTrip?.roomCount ?? Math.ceil(travelers / 2));
    const total = attractions + transport + food + hotel;
    return { attractions, transport, food, hotel, total, remaining: (displayTrip?.budgetLimit ?? session.tripState.budgetLimit) - total };
  })();
  const dayTabSummaries: Record<string, { total: number }> = {};
  if (session && catalog) {
    const updates = previewing && proposal ? proposal.updates ?? (proposal.targetRegionId !== 'all' ? [{ regionId: proposal.targetRegionId, nextUI: proposal.nextUI, nextData: proposal.nextData }] : []) : [];
    const travelers = displayTrip?.travelers ?? 1;
    const roomCount = displayTrip?.roomCount ?? Math.ceil(travelers / 2);
    const nights = displayTrip?.nights ?? session.tripState.nights;
    dayItems.forEach((item, index) => {
      const itemDay = updates.find(update => update.regionId === item.region)?.nextData ?? session.tripState.days[item.key];
      const places = itemDay.stops.map(stop => placeById(stop.placeId));
      const attractions = places.reduce((sum, place) => sum + (place?.cost ?? 0), 0) * travelers;
      const transport = places.reduce((sum, place) => sum + (place?.transportCost ?? 0), 0) * travelers;
      const food = (catalog.pricing?.foodPerPersonDay ?? 180) * travelers;
      const hotel = index < nights ? (selectedHotel?.nightlyPrice ?? 0) * roomCount : 0;
      dayTabSummaries[item.key] = {
        total: attractions + transport + food + hotel,
      };
    });
  }

  const allCandidateUpdates = proposal && !staleProposal ? proposal.updates ?? [] : [];
  const combinedDays = session ? { ...session.tripState.days } : undefined;
  const combinedRegions = session ? { ...session.uiSpec.regions } : undefined;
  if (previewing && combinedDays && combinedRegions) for (const update of allCandidateUpdates) {
    const key = dayItems.find(item => item.region === update.regionId)!.key;
    if (update.nextData) combinedDays[key] = update.nextData;
    combinedRegions[update.regionId] = update.nextUI;
  }
  const surfaceSession = session && displayTrip && combinedDays && combinedRegions ? {
    ...session, tripState: { ...displayTrip, days: combinedDays }, derived: displayDerived,
    uiSpec: { regions: combinedRegions },
  } : session;
  const surfacesToCheck = allCandidateUpdates.filter(update => !!update.nextUI.surface);
  const allSurfacesReady = !proposal || surfacesToCheck.every(update => surfaceReady[`${proposal.proposalId}-${update.regionId}`]);
  // Scoped editing never mounts legacy model-generated HTML/JavaScript, including hidden preflight frames.
  // Stored surfaces remain in SQLite for audit/compatibility; trusted native cards render below.
  const surfaceChecks = null;
  const generatedSurface = null;

  const guideVisible = activePage === 'guide';
  const setupVisible = !guideVisible && !loading && !connectionError && (showSetup || !session);

  return <div className={`app-shell ${setupVisible ? 'is-setup' : ''} ${guideVisible ? 'is-guide' : ''} ${editorMode ? 'editor-mode' : ''}`}>
    <header className="topbar"><div className="topbar-inner">
      <a className="brand" href="#workspace" data-testid="brand-home" aria-label="返回首页" onClick={event => { event.preventDefault(); if (setupVisible && !guideVisible) { document.querySelector<HTMLElement>('.page-container')?.scrollTo({ top: 0, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }); return; } if (openTripSetup()) requestAnimationFrame(() => document.querySelector<HTMLElement>('.page-container')?.scrollTo({ top: 0, behavior: 'auto' })); }}><span className="brand-symbol"><Compass size={25} strokeWidth={1.5} /></span><span className="brand-name">旅序<span className="brand-en">TRIP STUDIO</span></span></a>
      <nav className="topbar-nav" aria-label="主导航"><a className={`nav-link ${!guideVisible ? 'active' : ''}`} href="#workspace" aria-current={!guideVisible ? 'page' : undefined} onClick={event => { event.preventDefault(); setActivePage('planner'); }}><LayoutGrid size={14} />行程规划</a><a className={`nav-link ${guideVisible ? 'active' : ''}`} href="#guide" data-testid="guide-tab" aria-current={guideVisible ? 'page' : undefined} onClick={event => { event.preventDefault(); setActivePage('guide'); }}><BookOpen size={14} />指南</a></nav>
      <div className="topbar-actions">
        <button className={`button ghost mobile-guide-tab ${guideVisible ? 'active' : ''}`} type="button" aria-label={guideVisible ? '返回行程规划' : '打开指南'} onClick={() => setActivePage(guideVisible ? 'planner' : 'guide')}><BookOpen size={16} /><span>{guideVisible ? '规划' : '指南'}</span></button>
        {session && !setupVisible && !guideVisible && <div className="topbar-workspace-actions" role="group" aria-label="行程操作"><button className="button secondary topbar-new-trip" aria-label="规划新行程" onClick={openTripSetup} disabled={!!busy || hasDrafts}><Plus size={16} /><span>新建行程</span></button><button className="button ghost topbar-history" aria-label="操作记录" onClick={() => setHistoryOpen(true)}><History size={16} /><span>操作记录</span></button><button className="button ghost export-button" aria-label="导出计划" onClick={exportPlan} disabled={!!busy}><ArrowDownToLine size={16} /><span>导出</span></button></div>}
        <button className={`button secondary service-settings-button ${serviceConnectionClass}`} type="button" data-testid="service-settings-button" aria-label="打开服务接入" title={serviceConnectionTitle} onClick={() => openServiceSettings('model')}><Settings2 size={16} /><span>服务接入</span><i className="service-status-dot" aria-hidden="true" /></button>
      </div>
    </div></header>
    <main className="page-container" id="workspace">{guideVisible ? catalog ? <GuidePage catalog={catalog} city={session?.tripState.city} onBack={() => setActivePage('planner')} /> : <section className="connection-state" role="status"><LoaderCircle className="spin" size={30} /><h2>正在打开指南</h2><p>载入路线与地点内容…</p></section> : setupVisible ? <TripSetup status={agentStatus} previous={session} busy={busy === '正在规划行程'} error={setupError} onSubmit={settings => void planTrip(settings)} onContinue={() => { setActivePage('planner'); setShowSetup(false); }} onDemo={settings => void openDemoTrip(settings)} onNeedModel={() => openServiceSettings('model')} onAbort={() => actionController.current?.abort()} /> : <>
      <section className={`trip-overview ${tripOverviewCollapsed ? 'is-collapsed' : ''}`} aria-label="行程概览" data-testid="trip-overview">
        <div className="hero" id="trip-overview-details" aria-hidden={tripOverviewCollapsed}>
          <div className="hero-copy"><div className="hero-eyebrow"><Leaf size={14} /><span>慢一点，遇见更多风景</span></div><h1>{session?.tripState.city || '你的下一站'}，<span>{dayItems.length ? `${dayItems.length} 天，按你的节奏。` : '从这里出发。'}</span></h1><p>行程随你调整，每一次修改都有迹可循。</p><div className="trip-chips"><span className="chip"><CalendarDays size={14} />{dayItems.length} 天 {displayTrip?.nights ?? 0} 晚</span><span className="chip"><MapPin size={14} />{displayTrip?.city || '目的地待选择'} · {displayTrip?.travelers ?? 1} 人</span><span className="chip"><Wallet size={14} />预算 {money(displayTrip?.budgetLimit ?? 2600)}</span></div></div>
          <div className="hero-art"><ScenicIllustration /><span className="art-caption">A LITTLE ESCAPE, AT YOUR OWN PACE.</span></div>
          <button className="hero-collapse-toggle" type="button" aria-controls="trip-overview-details" aria-expanded={!tripOverviewCollapsed} tabIndex={tripOverviewCollapsed ? -1 : 0} onClick={() => setTripOverviewCollapsed(true)} data-testid="trip-overview-collapse"><ChevronDown size={15} />收起概览</button>
        </div>
        <button className="hero-compact-toggle" type="button" aria-controls="trip-overview-details" aria-expanded={!tripOverviewCollapsed} aria-hidden={!tripOverviewCollapsed} tabIndex={tripOverviewCollapsed ? 0 : -1} onClick={() => setTripOverviewCollapsed(false)} data-testid="trip-overview-expand">
          <span className="hero-compact-title"><MapPin size={15} /><strong>{displayTrip?.city || '目的地待选择'}</strong><span>· {dayItems.length} 天，按你的节奏</span></span>
          <span className="hero-compact-meta"><CalendarDays size={14} />{dayItems.length} 天 {displayTrip?.nights ?? 0} 晚<span aria-hidden="true">·</span><Wallet size={14} />预算 {money(displayTrip?.budgetLimit ?? 2600)}</span>
          <span className="hero-compact-action">展开概览<ChevronDown size={15} /></span>
        </button>
      </section>

      {loading ? <section className="connection-state" role="status"><LoaderCircle className="spin" size={30} /><h2>正在打开你的行程</h2><p>载入行程与上次保存的修改…</p></section> : connectionError || !session || !catalog ? <section className="connection-state" role="alert"><RefreshCw size={30} /><h2>暂时没有连接上服务</h2><p>{connectionError || '行程数据载入失败。'}</p><button className="button primary" onClick={() => setReloadKey(value => value + 1)}><RefreshCw size={16} />重新连接</button></section> : <>
      <div className="workspace-grid">
        <div className="itinerary-column" data-testid="itinerary-scroll" role="region" aria-label="行程内容" tabIndex={0}>
          {proposal && !staleProposal && <div ref={previewSurface} className={`candidate-preview-banner ${previewing ? '' : 'showing-current'}`} data-testid="candidate-preview-banner" role="status">
            <div><span className="candidate-preview-label"><LayoutGrid size={15} />{previewing ? '候选预览，尚未应用' : '当前已保存的界面'}</span><strong>{previewing ? proposal.title || '所选修改方案' : '比较后再决定是否修改'}</strong><p>{previewing ? '左侧展示应用后的实际样式。预览期间不能直接编辑，已有行程仍保持不变。' : '这里是当前保存的行程，可返回任意候选继续比较。'}</p></div>
            <div className="candidate-preview-tabs" aria-label="快速切换候选">{candidateOptions.map((candidate, index) => <button key={candidate.proposalId} className={previewing && proposal.proposalId === candidate.proposalId ? 'active' : ''} data-testid={`preview-candidate-${index + 1}`} disabled={!!busy} aria-pressed={previewing && proposal.proposalId === candidate.proposalId} onClick={() => selectCandidate(candidate)}>方案 {index + 1}</button>)}<button onClick={() => setShowCandidatePreview(false)} disabled={!!busy} aria-pressed={!previewing}>当前界面</button></div>
            <div className="candidate-preview-links"><button onClick={() => revealInPane(proposalPanel.current)}>返回方案选择</button><button data-testid="candidate-preview-close" onClick={cancelProposal} disabled={!!busy}>取消预览</button></div>
          </div>}
          {!!(session.planningWarnings ?? session.planningSettings?.warnings)?.length && <div className="planning-notices" role="status">{[...new Set(session.planningWarnings ?? session.planningSettings?.warnings)].map((warning, index) => <p key={index}>{warning}</p>)}</div>}
          <div className="section-heading"><div><span className="section-eyebrow">YOUR ITINERARY</span><h2>把时间留给喜欢的事</h2></div><span className="saved-state"><CheckCircle2 size={14} />{busy ? '保存 / 处理中' : previewing ? '候选尚未应用' : hasDrafts ? '有尚未保存的输入' : '修改已保存'}</span></div>
          <div className="editor-workspace-toolbar" aria-label="工作模式"><div><ShieldCheck size={16} /><strong>工作模式</strong><span>{editorMode ? '选择组件，使用右侧手动编辑工具' : '查看行程，或让右侧 Agent 帮你调整'}</span></div><div role="group" aria-label="切换工作模式"><button aria-pressed={!editorMode} disabled={!!busy} onClick={() => setEditingMode(false)} data-testid="browse-mode-toggle">浏览模式</button><button aria-pressed={editorMode} disabled={!!busy} onClick={() => setEditingMode(true)} data-testid="edit-mode-toggle">手动编辑</button></div></div>
          <div className="day-tabs" role="tablist" aria-label="选择行程日期">{dayItems.map(item => { const summary = dayTabSummaries[item.key]; return <button key={item.key} id={`tab-${item.key}`} role="tab" aria-selected={selectedDay === item.key} aria-controls={`panel-${item.key}`} className={`day-tab ${selectedDay === item.key ? 'active' : ''}`} onClick={() => changeDay(item.key, item.region)} disabled={!!busy}><span className="day-tab-number">{item.number}</span><span className="day-tab-copy"><strong>{item.label}</strong><small>{session.tripState.days[item.key].date}</small></span>{summary && <span className="day-tab-budget">{money(summary.total)}</span>}{(session.controlState.lockedRegions.includes(item.region) || isComponentLocked(session, bindingFor(session, item.key)?.componentId, false)) && <LockKeyhole size={13} />}</button>; })}</div>
          {day && ui && <div data-testid={previewing ? 'candidate-preview-region' : undefined}><section className={`day-panel ${regionLocked ? 'is-locked' : ''} ${previewing ? 'is-candidate-preview' : ''}`} id={`panel-${selectedDay}`} data-testid={`region-${activeRegion}`} data-preview={previewing ? 'true' : 'false'} data-candidate-id={previewing ? proposal.proposalId : undefined} role="tabpanel" aria-labelledby={`tab-${selectedDay}`} {...selectionAttributes(activeDayBinding, editorMode, selectedComponentId, isComponentLocked(session, activeDayBinding?.componentId), selectEditorComponent)}>
            <div className="day-panel-header"><div><span className="panel-eyebrow">DAY {dayItem.number}<span className="eyebrow-line" /></span><h3>{day.title}</h3><p>{day.stops.length} 个停留点<span>·</span>按自己的节奏，慢慢走</p></div>{previewing ? <span className="preview-readonly-status"><ShieldCheck size={14} />只读预览</span> : <button className={`icon-button generation-lock-button ${regionLocked ? 'locked' : ''}`} title={regionLocked ? '解除本日生成锁定' : '锁定本日生成修改'} aria-label={`${regionLocked ? '解锁' : '锁定'}${dayItem.label}的生成修改`} aria-pressed={regionLocked} onClick={() => toggleLock(activeRegion, !regionLocked)} disabled={!!busy}>{regionLocked ? <LockKeyhole size={15} /> : <UnlockKeyhole size={15} />}</button>}</div>
            {previewing ? <div className="day-toolbar preview-toolbar"><span><LayoutGrid size={14} />{ui.view === 'timeline' ? '时间轴展示' : '卡片展示'} · {ui.compact ? '精简信息' : '完整信息'}</span><span>{candidateUpdate ? '本日包含候选修改' : '本日不在修改范围内，显示原有内容'}</span></div> : <div className="day-toolbar"><span className="weather-note"><Sun size={15} />费用为预算估算</span><button className="button secondary route-refresh-button" data-testid="refresh-day-routes" aria-label={`${dayItem.label}添加或刷新交通与地图`} disabled={!!busy || hasDrafts} onClick={() => void refreshRoutes(activeRegion)}>{busy === '正在查询路线' ? <LoaderCircle className="spin" size={14} /> : <MapPin size={14} />}{busy === '正在查询路线' ? '正在查询…' : ui.showMap || ui.showTransport ? '刷新交通与地图' : '添加交通与地图'}</button><div className="view-switch" aria-label="展示方式"><button className={ui.view === 'cards' ? 'active' : ''} onClick={() => previewView('cards')} disabled={!!busy || regionLocked || dayComponentLocked || ui.view === 'cards' || hasDrafts} aria-label="预览卡片展示"><LayoutGrid size={14} />卡片</button><button className={ui.view === 'timeline' ? 'active' : ''} onClick={() => previewView('timeline')} disabled={!!busy || regionLocked || dayComponentLocked || ui.view === 'timeline' || hasDrafts} aria-label="预览时间轴展示"><Clock3 size={14} />时间轴</button></div></div>}
            {ui.surface?.placement === 'beforeStops' && generatedSurface}
            {ui.surface && <p className="editor-legacy-notice" role="status">旧版生成代码已保留但不会执行；当前显示可信原生行程卡片。</p>}
            {<>
            {ui.mapPlacement === 'beforeStops' && routeMap}
            {editorMode && <div className="editor-periods" aria-label={dayItem.label + '时段选区'}>{(['am', 'pm', 'evening'] as const).map(period => {
              const binding = bindingFor(session, selectedDay, undefined, period);
              if (!binding) return null;
              const locked = isComponentLocked(session, binding.componentId);
              return <button key={binding.componentId} type="button" data-component-id={binding.componentId} data-editor-label={binding.label} data-editor-selected={selectedComponentId === binding.componentId ? 'true' : undefined} data-editor-locked={locked ? 'true' : undefined} aria-pressed={selectedComponentId === binding.componentId} aria-label={'选择' + dayItem.label + PERIOD_LABELS[period]} data-testid={'editor-period-' + period} onClick={() => selectEditorComponent(binding.componentId)} disabled={!!busy}>{PERIOD_LABELS[period]}{locked && <LockKeyhole size={12} />}</button>;
            })}</div>}
            <div className={`stops-list ${ui.view === 'timeline' ? 'timeline' : 'cards'} ${ui.compact ? 'compact' : ''}`}>
              {day.stops.length ? day.stops.map((stop, index) => { const place = placeById(stop.placeId); const options = catalog.places.filter(candidate => candidate.id === stop.placeId || !day.stops.some(other => other.id !== stop.id && other.placeId === candidate.id)); const stopBinding = bindingFor(session, selectedDay, stop.id); const stopComponentLocked = isComponentLocked(session, stopBinding?.componentId, false); return <Fragment key={stop.id}><article className={`stop-card ${place?.indoor ? 'indoor' : 'outdoor'}`} data-stop-id={stop.id} data-editor-in-scope={editorMode && selectedBinding?.period && !selectedBinding.stopId && selectedBinding.period === stopBinding?.period && selectedBinding.dayKey === selectedDay ? 'true' : undefined} {...selectionAttributes(stopBinding, editorMode, selectedComponentId, isComponentLocked(session, stopBinding?.componentId), selectEditorComponent)}>
                {editorMode && isComponentLocked(session, stopBinding?.componentId) && <span className="editor-card-lock" aria-label="此景点组件已锁定"><LockKeyhole size={11} />已锁定</span>}
                <div className="stop-time"><span className="timeline-dot" />{previewing ? <span className="preview-stop-time">{stop.startTime}</span> : <label className="time-label"><span className="sr-only">调整{place?.name ?? '景点'}开始时间</span><input type="time" value={timeDrafts[stop.id] ?? stop.startTime} onChange={event => { setTimeDrafts(drafts => ({ ...drafts, [stop.id]: event.target.value })); markEdited(); }} onBlur={() => saveTime(stop.id)} disabled={!!busy || stopComponentLocked} aria-label={`调整${place?.name ?? '景点'}开始时间`} /></label>}<span className="stop-index">停留 {String(index + 1).padStart(2, '0')}</span></div>
                <StopCardContent stop={stop} place={place} showCost={ui.showCost} compact={ui.compact}>{previewing ? <p className="preview-preserved-hint">候选中的景点选择，应用后可继续手动调整</p> : <div className="stop-alternative"><label htmlFor={`alternative-${stop.id}`}>换个景点<span>当日已选地点已从备选中排除</span></label><div className="select-wrap"><select id={`alternative-${stop.id}`} data-testid={`alternative-${stop.id}`} aria-label={`替换${place?.name ?? '当前景点'}的备选景点`} value={stop.placeId} disabled={!!busy || hasDrafts || stopComponentLocked} onChange={event => { if (event.target.value !== stop.placeId) direct({ kind: 'replace_stop', targetRegionId: activeRegion, stopId: stop.id, value: event.target.value }, '景点选择已保存，预算与路线状态已同步。'); }}><option value={stop.placeId}>当前 · {place?.name ?? '当前景点'}</option>{[false, true].map(indoor => { const group = options.filter(candidate => candidate.id !== stop.placeId && candidate.indoor === indoor); return group.length ? <optgroup label={indoor ? '室内与展馆' : '户外与街区'} key={String(indoor)}>{group.map(candidate => <option value={candidate.id} key={candidate.id}>{candidate.name}</option>)}</optgroup> : null; })}</select><ChevronDown size={14} /></div></div>}</StopCardContent>
              </article>{ui.showTransport && ui.transportStyle !== 'summary' && index < day.stops.length - 1 && <TransportLeg segment={routeData?.segments.find(segment => segment.fromStopId === stop.id && segment.toStopId === day.stops[index + 1].id)} fromName={place?.name ?? '当前景点'} toName={placeById(day.stops[index + 1].placeId)?.name ?? '下一景点'} mode={ui.transportMode ?? 'transit'} message={routeData?.message} />}</Fragment>; }) : <div className="empty-state"><MapPin size={24} /><p>这一天还没有安排景点。</p></div>}
            </div>
            {ui.showTransport && ui.transportStyle === 'summary' && <section className="transport-summary" aria-label={`${dayItem.label}交通汇总`} data-testid="transport-summary"><div className="route-section-heading"><h4><Bus size={17} />今天的交通安排</h4><span className="map-source">按行程顺序汇总</span></div>{day.stops.slice(0, -1).map((stop, index) => <TransportLeg key={stop.id} segment={routeData?.segments.find(segment => segment.fromStopId === stop.id && segment.toStopId === day.stops[index + 1].id)} fromName={placeById(stop.placeId)?.name ?? '当前景点'} toName={placeById(day.stops[index + 1].placeId)?.name ?? '下一景点'} mode={ui.transportMode ?? 'transit'} message={routeData?.message} />)}</section>}
            {ui.showAlternatives && <section className="alternatives-section"><div className="alternatives-heading"><div><span className="mini-eyebrow">PLAN B</span><h4><Umbrella size={16} />雨天也有好去处</h4></div>{!previewing && <label className="replace-target-label">替换到<select aria-label="选择要替换的行程" value={replaceStopId} onChange={event => setReplaceStopId(event.target.value)} disabled={!!busy}>{day.stops.map(stop => <option key={stop.id} value={stop.id}>{stop.startTime} · {placeById(stop.placeId)?.name}</option>)}</select></label>}</div><div className="table-scroll"><table className="alternatives-table"><thead><tr><th>室内备选景点</th><th>游览时长</th><th>门票预算</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>{alternatives.slice(0, 4).map(place => <tr key={place.id}><td><strong>{place.name}</strong><small>{place.location}</small></td><td>{place.durationMinutes} 分钟</td><td>{money(place.cost)}</td><td>{previewing ? <span className="preview-table-label">备选景点</span> : <button className="button table-action" disabled={!!busy || !replaceStopId} aria-label={`用${place.name}替换${replaceName}`} onClick={() => direct({ kind: 'replace_stop', targetRegionId: activeRegion, stopId: replaceStopId, value: place.id }, `已将${replaceName}替换为${place.name}，预算同步更新。`)}>替换<ArrowRight size={13} /></button>}</td></tr>)}</tbody></table>{!alternatives.length && <p className="empty-hint">当前没有更多可用的室内备选景点。</p>}</div></section>}
            {ui.mapPlacement !== 'beforeStops' && routeMap}
            </>}
            {ui.surface?.placement === 'afterStops' && generatedSurface}
            <GeneratedBlocks blocks={ui.blocks} />
            {previewing ? <div className="day-note preview-day-note"><span className="field-label">这一天的备注</span><p>{day.note || '尚未填写备注'}</p></div> : <div className="day-note"><label className="field-label" htmlFor={`note-${selectedDay}`}><span><Plus size={14} />给这一天留个备注</span><small>移开输入框后保存</small></label><textarea id={`note-${selectedDay}`} key={`note-${selectedDay}`} rows={2} maxLength={2000} value={noteDrafts[selectedDay] ?? day.note} aria-label={`${dayItem.label}行程备注`} placeholder="例如：下午留些时间喝茶，节奏不用太赶。" onChange={event => { setNoteDrafts(drafts => ({ ...drafts, [selectedDay]: event.target.value })); markEdited(); }} onBlur={() => saveNote(selectedDay, activeRegion)} disabled={!!busy || dayComponentLocked} /></div>}
            <div className="day-panel-footer"><ShieldCheck size={14} /><span>{previewing ? '当前仅预览候选，原有行程与输入尚未改变。' : dayComponentLocked ? '本日持久组件锁已生效，请先解除锁定再修改。' : regionLocked ? '本区域已保护：生成不会修改；直接操作仍由你决定。' : '界面与行程数据分别保存，切换展示仍可继续操作。'}</span><span className="region-id">{activeRegion}</span></div>
          </section></div>}
          <div className="details-grid"><section className="hotel-card"><div className="card-heading"><span className="card-icon"><MapPin size={18} /></span><h3>住得舒服一点</h3>{previewing ? <span className="preview-readonly-status">{proposal?.nextTrip?.hotelId ? '候选酒店，尚未应用' : '原有选择保留'}</span> : <button className={`icon-button ${session.controlState.hotelLocked ? 'locked' : ''}`} aria-label={session.controlState.hotelLocked ? '解锁酒店选择' : '锁定酒店选择'} aria-pressed={session.controlState.hotelLocked} title="锁定后保留当前酒店选择" disabled={!!busy} onClick={() => toggleLock('hotel', !session.controlState.hotelLocked)}>{session.controlState.hotelLocked ? <LockKeyhole size={16} /> : <UnlockKeyhole size={16} />}</button>}</div>{previewing ? <p className="preview-hotel-name">{selectedHotel?.name}</p> : <><label className="sr-only" htmlFor="hotel-select">选择酒店</label><div className="select-wrap"><select id="hotel-select" value={session.tripState.hotelId} disabled={!!busy || session.controlState.hotelLocked} onChange={event => direct({ kind: 'hotel', value: event.target.value }, '酒店选择已保存，住宿预算同步更新。')}>{hotelGroups.map(group => <optgroup label={group.label} key={group.label}>{group.hotels.map(hotel => <option key={hotel.id} value={hotel.id}>{hotel.name} · {money(hotel.nightlyPrice)}/晚</option>)}</optgroup>)}</select><ChevronDown size={15} /></div></>}<p className="hotel-description">{selectedHotel?.description}</p>{selectedHotel?.address && <p className="place-address hotel-address"><MapPin size={12} /><span>{selectedHotel.address}</span></p>}{officialLink(selectedHotel?.sourceUrl) && <a className="place-source-link" href={officialLink(selectedHotel?.sourceUrl)} target="_blank" rel="noopener noreferrer">{selectedHotel?.sourceName || '酒店官方信息'}<ArrowUpRight size={12} /></a>}<div className="hotel-bottom"><span>{selectedHotel?.location}<small>入住 {displayTrip?.nights ?? session.tripState.nights} 晚 · {displayTrip?.roomCount ?? Math.ceil((displayTrip?.travelers ?? 1) / 2)} 间</small></span><strong><span className="hotel-price-label">住宿预算估算</span>{money(selectedHotel?.nightlyPrice ?? 0)}<small> / 晚</small>{selectedHotel?.nightlyPriceRange && <small className="hotel-price-range">常规规划区间 {money(selectedHotel.nightlyPriceRange[0])}–{money(selectedHotel.nightlyPriceRange[1])}</small>}</strong></div>{selectedHotel?.priceNote && <p className="hotel-price-note">{selectedHotel.priceNote}</p>}{session.controlState.hotelLocked && <p className="inline-lock"><LockKeyhole size={12} />酒店选择已锁定</p>}</section>
          <section data-testid="budget-card" className={`budget-card ${displayDerived.remaining < 0 ? 'over-budget' : ''}`}><div className="card-heading"><span className="card-icon"><Wallet size={18} /></span><h3>每一笔，都心中有数</h3><span className="budget-caption">预算估算</span></div><div className="budget-overview"><strong data-testid="budget-total">{money(displayDerived.total)}</strong><label htmlFor="budget-limit">/ 预算 ¥{previewing ? <span className="preview-budget-limit">{displayTrip?.budgetLimit}</span> : <input id="budget-limit" aria-label="总预算上限" type="number" inputMode="numeric" min={100} max={100000} value={budgetDraft} onChange={event => { setBudgetDraft(event.target.value); markEdited(); }} onBlur={saveBudget} onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); }} disabled={!!busy} />}</label></div><div className="budget-progress" role="progressbar" aria-label="预算使用比例" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(100, Math.round(displayDerived.total / (displayTrip?.budgetLimit ?? session.tripState.budgetLimit) * 100))}><span style={{ width: `${Math.min(100, displayDerived.total / (displayTrip?.budgetLimit ?? session.tripState.budgetLimit) * 100)}%` }} /></div>{session.presentationSettings?.showBudgetDetails !== false && <div className="budget-breakdown" data-testid="budget-breakdown"><span><i className="dot hotel-dot" />住宿<strong>{money(displayDerived.hotel)}</strong></span><span><i className="dot ticket-dot" />门票<strong>{money(displayDerived.attractions)}</strong></span><span><i className="dot transport-dot" />交通<strong>{money(displayDerived.transport)}</strong></span><span><i className="dot food-dot" />餐饮<strong>{money(displayDerived.food)}</strong></span></div>}<p className="budget-remaining">{displayDerived.remaining < 0 ? `已超出预算 ${money(-displayDerived.remaining)}，可以调整酒店或景点。` : `还留有 ${money(displayDerived.remaining)} 的从容空间。`}</p>{catalog.pricing?.notice && <p className="budget-pricing-note">{catalog.pricing.notice}</p>}</section></div>
        </div>
        <aside className={`control-panel ${editorMode ? 'manual-control-panel' : 'browse-control-panel'}`} data-testid="agent-scroll" aria-label={editorMode ? '手动编辑工具' : 'Agent 行程对话'} tabIndex={0}>{editorMode ? <>
          <LocalEditor session={session} mode={editorMode} selectedId={selectedComponentId} busy={!!busy || hasDrafts} onSelect={selectEditorComponent} onLock={lockEditorComponent} onApply={applyEditorPatch} onDisplay={applyEditorDisplay} onError={error => void recoverError(error)} />
          <GlobalControls session={session} busy={!!busy} hasDrafts={hasDrafts} onPresentation={applyGlobalPresentation} onConstraint={applyBudgetConstraint} />
          <CandidatePanel key={`manual-candidate-${serviceRevision}`} session={session} catalog={catalog} selectedId={selectedComponentId} editorMode={editorMode} busy={!!busy} hasDrafts={hasDrafts} onCommit={applyEditCandidate} onConflict={error => void recoverError(error)} onNeedModel={() => openServiceSettings('model')} />
          {proposal ? <section ref={proposalPanel} id="proposal-comparison" className={`proposal-card ${staleProposal ? 'is-stale' : ''}`} aria-live="polite" data-testid="candidate-comparison">
            <div className="proposal-heading"><span><CheckCircle2 size={16} />{staleProposal ? '方案需要重新预览' : candidateOptions.length === 1 ? '1 个修改方案' : `${candidateOptions.length} 个候选界面`}</span><span className="badge">{candidateOptions.length === 1 ? '待确认应用' : '待选择应用'}</span></div>
            <p className="candidate-intro">{candidateOptions.length === 1 ? '左侧展示本次修改方案，确认效果后可应用。' : '选择候选可在左侧查看实际界面。所有方案都以当前已保存的行程为基础。'}</p>
            <section className="candidate-scope" data-testid="candidate-scope" aria-label="本次允许修改"><h3>本次允许修改</h3>{scopeSummary.length ? <ul>{scopeSummary.map((item, index) => <li key={index}>{item}</li>)}</ul> : <p>{proposalRegions.map(region => dayItems.find(item => item.region === region)?.label ?? region).join('、')}中的本次需求</p>}</section>
            {!!preservedSummary.length && <section className="candidate-scope candidate-preserved" aria-label="保持不变"><h3><ShieldCheck size={13} />保持不变</h3><ul>{preservedSummary.map((item, index) => <li key={index}>{item}</li>)}</ul></section>}
            <div className="candidate-options" role="radiogroup" aria-label="选择候选界面">{candidateOptions.map((candidate, index) => <label key={candidate.proposalId} data-testid="candidate-option" className={`candidate-option ${proposal.proposalId === candidate.proposalId ? 'is-selected' : ''} ${staleProposal ? 'is-disabled' : ''}`}><input type="radio" name="candidate-interface" data-testid={`candidate-option-${index + 1}`} value={candidate.proposalId} checked={proposal.proposalId === candidate.proposalId} disabled={!!busy || staleProposal} onChange={() => selectCandidate(candidate)} /><span><strong>方案 {index + 1} · {candidate.title || `候选界面 ${index + 1}`}</strong><small>{candidate.rationale || candidate.summary}</small><em>{candidate.changeCount ?? candidate.changes.length} 项改动{proposal.proposalId === candidate.proposalId ? ' · 已选中' : ''}</em></span></label>)}</div>
            {candidateOptions.length === 1 && !candidateNotes.length && <p className="candidate-note">本次按单方案模式生成，确认预览后即可应用。</p>}{candidateNotes.map((note, index) => <p className="candidate-note" key={index}>{note}</p>)}
            <p className="proposal-summary">{proposal.summary}</p><div className="proposal-scope" aria-label="本次影响日期"><span>本次更新</span>{proposalRegions.map(region => <strong key={region}>{dayItems.find(item => item.region === region)?.label ?? region}</strong>)}</div>
            <ul className="proposal-changes">{proposal.changes.map((change, index) => <li key={`${proposal.proposalId}-${index}`}><Check size={13} />{change}</li>)}</ul>{proposal.warnings.map((warning, index) => <p className="proposal-warning" key={index}>{warning}</p>)}
            {!!proposal.trace?.length && <details className="agent-trace"><summary>查看执行步骤 · {proposal.trace.length} 项</summary><ol>{proposal.trace.map((event, index) => <li key={index}><CheckCircle2 size={13} /><div><strong>{event.step}</strong><p>{event.detail}</p></div></li>)}</ol></details>}
            {staleProposal && <p className="proposal-warning">需求、输入或页面状态已变化。请重新预览，避免应用旧方案。</p>}
            {surfaceErrors[proposal.proposalId] && <div className="proposal-warning" role="alert"><p>生成界面运行失败：{surfaceErrors[proposal.proposalId]}</p><button className="button secondary" disabled={!!busy || staleProposal} onClick={() => void repairSurface(surfaceErrors[proposal.proposalId])}><RefreshCw size={14} />让Agent修复</button></div>}
            <div className="proposal-actions"><button className="button primary" onClick={() => void applyProposal()} disabled={!!busy || staleProposal || !!surfaceErrors[proposal.proposalId] || !allSurfacesReady}><Check size={15} />{allSurfacesReady ? '应用这次修改' : '正在检查生成界面…'}</button><button className="button ghost" disabled={!!busy} onClick={cancelProposal}>取消</button></div>
          </section> : null}
          <div className="control-footer"><div className="control-trust"><ShieldCheck size={16} /><span>锁定可保护 · 修改可预览 · 操作可撤销</span></div><button className="button secondary all-route-button" data-testid="refresh-all-routes" aria-label="为全部日期添加或刷新交通与地图" disabled={!!busy || hasDrafts || previewing} onClick={() => void refreshRoutes('all')}>{busy === '正在查询路线' ? <LoaderCircle className="spin" size={15} /> : <Bus size={15} />}查询全部日期交通与地图</button>{busy === '正在查询路线' && <p className="route-query-progress" role="status">正在核实地点并查询交通路线，已有输入会保留。</p>}<button className="button secondary undo-button" onClick={() => { markEdited(); void mutate('正在撤销', (current, signal) => api.undo(current.id, current.version, signal), '已在新版本恢复上一步的数据、展示、组件锁与硬约束。', true); }} disabled={!!busy || !session.canUndo || hasDrafts || previewing}><RotateCcw size={15} />撤销上一步</button><p className="undo-semantics">撤销将同步恢复上一个版本的数据、展示、组件锁和硬约束，并创建新版本。</p><button className="button ghost mobile-history" onClick={() => setHistoryOpen(true)}><History size={15} />查看操作记录</button><p className="demo-note">{agentDemo ? '当前使用演示模式，不会调用模型。' : '候选仅生成受限 Patch；旧生成代码不会执行。路线与行程保存由后台提供。'}费用为预算估算，实际票价、房价和开放情况以官方为准。交通路线来自地图查询。</p></div>
        </> : <CandidatePanel key={`browse-conversation-${serviceRevision}`} variant="conversation" session={session} catalog={catalog} selectedId={activeDayBinding?.componentId ?? null} editorMode={false} busy={!!busy} hasDrafts={hasDrafts} onCommit={applyEditCandidate} onConflict={error => void recoverError(error)} onNeedModel={() => openServiceSettings('model')} />}
        </aside>
      </div>
      {surfaceChecks}
      <footer className="page-footer"><span><Compass size={14} />旅序 · AI 行程工作台</span><span>让每一次变化，都在你的掌控中。</span><button className="reset-link" disabled={!!busy || previewing} onClick={() => setResetOpen(true)}><RefreshCw size={12} />重置行程</button></footer>
      </>}
    </>}</main>
    <div className="toast-stack" role="status" aria-live="polite">{toasts.map(toast => <div className={`toast ${toast.kind}`} key={toast.id}>{toast.kind === 'success' ? <CheckCircle2 size={17} /> : <ShieldCheck size={17} />}<span>{toast.message}</span><button aria-label="关闭提示" onClick={() => setToasts(items => items.filter(item => item.id !== toast.id))}><X size={14} /></button></div>)}</div>
    {serviceSettingsOpen && <Overlay className="service-settings-dialog" label="服务接入" onClose={() => setServiceSettingsOpen(false)}><ServiceSettings target={serviceSettingsTarget} onClose={() => setServiceSettingsOpen(false)} onChanged={integrationChanged} /></Overlay>}
    {historyOpen && session && <Overlay className="history-drawer" label="操作记录" onClose={() => { if (!busyRef.current) setHistoryOpen(false); }}><div className="drawer-header"><div><span className="panel-eyebrow">VERSION JOURNAL</span><h2>每一次修改，都有记录</h2></div><button className="icon-button" aria-label="关闭操作记录" disabled={!!busy} onClick={() => setHistoryOpen(false)}><X size={20} /></button></div><VersionHistory session={session} busy={!!busy || hasDrafts} onRestore={restoreSavedVersion} /><button className="button secondary drawer-close" disabled={!!busy} onClick={() => setHistoryOpen(false)}><ArrowLeft size={15} />返回行程</button></Overlay>}
    {resetOpen && <Overlay className="confirm-dialog" label="确认重置行程" onClose={() => { if (!busyRef.current) setResetOpen(false); }}><span className="dialog-icon"><RefreshCw size={24} /></span><h2>从最初的行程重新开始？</h2><p>当前行程、界面设置和锁定将恢复为本次规划的初始版本。尚未保存的输入也会清除。</p><div className="dialog-actions"><button className="button secondary" disabled={!!busy} onClick={() => setResetOpen(false)}>保留当前行程</button><button className="button primary" disabled={!!busy} onClick={() => { markEdited(); void mutate('正在重置', (current, signal) => api.reset(current.id, current.version, signal), '已恢复本次规划的初始行程。', true).then(ok => { if (ok) { setResetOpen(false); setProposal(null); setInstruction(''); setSelectedDay(sessionDayItems(sessionRef.current)[0]?.key ?? 'day1'); setTargetRegion(sessionDayItems(sessionRef.current)[0]?.region ?? 'day-1'); } }); }}>{busy ? '正在重置…' : '确认重置'}</button></div></Overlay>}
  </div>;
}


