import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, Code2 } from 'lucide-react';
import type { Catalog, DirectUpdate, GeneratedSurface, RegionId, RouteDay, Session, TripDay } from '../types';
import { createSurfaceDocument, SURFACE_MESSAGE } from './generatedSurfaceDocument';
import './GeneratedSurface.css';

export interface GeneratedSurfaceProps {
  surface: GeneratedSurface;
  day: TripDay;
  region: RegionId;
  session: Session;
  catalog: Catalog;
  route?: RouteDay;
  readOnly: boolean;
  proposalId?: string;
  validationOnly?: boolean;
  onReady?: () => void;
  onAction?: (update: DirectUpdate) => unknown | Promise<unknown>;
  onStateChange?: (state: Record<string, unknown>) => unknown | Promise<unknown>;
  onRuntimeError?: (message: string) => void;
}

function randomToken(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(20)), value => value.toString(16).padStart(2, '0')).join('');
}

export function isSurfaceState(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  let count = 0;
  const visit = (item: unknown, depth: number): boolean => {
    if (++count > 2000 || depth > 12) return false;
    if (item === null || typeof item === 'boolean' || typeof item === 'string') return true;
    if (typeof item === 'number') return Number.isFinite(item);
    if (Array.isArray(item)) return item.length <= 500 && item.every(child => visit(child, depth + 1));
    if (typeof item === 'object') return Object.entries(item as Record<string, unknown>).every(([key, child]) => !['__proto__', 'prototype', 'constructor'].includes(key) && visit(child, depth + 1));
    return false;
  };
  try { return JSON.stringify(value).length <= 20000 && visit(value, 0); } catch { return false; }
}

function readMapImage(response: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => typeof reader.result === 'string' ? resolve(reader.result) : reject(new Error('地图无法读取'));
    reader.onerror = () => reject(new Error('地图无法读取'));
    reader.readAsDataURL(response);
  });
}

export function GeneratedSurfaceView(props: GeneratedSurfaceProps) {
  const { surface, day, region, session, catalog, route, readOnly, proposalId, validationOnly = false } = props;
  const frame = useRef<HTMLIFrameElement>(null);
  const latest = useRef(props);
  latest.current = props;
  const connectFrame = useRef<(() => void) | null>(null);
  const privatePort = useRef<MessagePort | null>(null);
  const retryState = useRef<(() => void) | null>(null);
  const [height, setHeight] = useState(220);
  const [ready, setReady] = useState(false);
  const [runtimeError, setRuntimeError] = useState('');
  const [saveError, setSaveError] = useState('');
  const [mapImage, setMapImage] = useState('');
  const [mapIdentity, setMapIdentity] = useState('');
  const [mapMessage, setMapMessage] = useState('');
  const pendingAction = useRef(false);
  const surfaceKey = JSON.stringify(surface);
  const channel = useMemo(randomToken, [surfaceKey, region, session.id, proposalId]);
  const nonce = useMemo(randomToken, [channel]);
  const state = session.uiSpec.regions[region].surfaceState ?? {};
  const routeIdentity = JSON.stringify({ sessionId: session.id, region, proposalId, route });
  const needsMap = !validationOnly && /travel-map/i.test(surface.html + surface.javascript);
  const activeMapImage = mapIdentity === routeIdentity ? mapImage : '';
  const data = useMemo(() => ({ day, places: catalog.places, hotels: catalog.hotels, trip: session.tripState, derived: session.derived, route, readOnly, widgetState: state, mapImage: activeMapImage, mapMessage }), [day, catalog, session.tripState, session.derived, route, readOnly, state, activeMapImage, mapMessage]);
  // Host renders may clone trip/derived objects without changing their content.
  // Do not invoke authored onChange for those renders: it can rebuild DOM and
  // discard native details/focus/filter state.
  const dataKey = JSON.stringify(data);
  const latestData = useRef(data);
  latestData.current = data;
  const documentSource = useMemo(() => createSurfaceDocument(JSON.parse(surfaceKey) as GeneratedSurface, channel, latestData.current, nonce), [surfaceKey, channel, nonce]);

  useEffect(() => {
    let disposed = false;
    let rendered = false;
    let hadRuntimeError = false;
    let inFlightState = false;
    let queuedState: { value: Record<string, unknown>; requestId: string; callback: NonNullable<GeneratedSurfaceProps['onStateChange']> } | null = null;
    const activePorts = new Set<MessagePort>();
    setReady(false); setRuntimeError(''); setSaveError(''); setHeight(220); pendingAction.current = false;
    const error = (message: string) => {
      if (disposed) return;
      hadRuntimeError = true;
      const safe = message.slice(0, 1000);
      setRuntimeError(safe); latest.current.onRuntimeError?.(safe);
    };
    const timer = setTimeout(() => error('生成界面未完成启动，请重新生成或修复此界面。'), 10000);
    const post = (type: string, payload: object = {}) => { if (!disposed) privatePort.current?.postMessage({ type, ...payload }); };
    const result = (requestId: string, status: 'saved' | 'failed', message: string) => post('result', { requestId, status, message });
    const drainState = async () => {
      if (inFlightState) return;
      inFlightState = true;
      while (queuedState) {
        const job = queuedState;
        queuedState = null;
        try {
          // The callback is captured when this user request arrives, so a later
          // day change cannot redirect the queued save into another region.
          await job.callback(job.value);
          if (!disposed) setSaveError('');
          result(job.requestId, 'saved', '已保存交互状态。');
        } catch {
          // Keep the most recent full snapshot for an explicit retry. Already
          // started writes continue even if this component has been unmounted.
          if (!queuedState) queuedState = job;
          if (!disposed) setSaveError('交互状态尚未保存，请重试；当前勾选仍保留在本页。');
          result(job.requestId, 'failed', '交互状态尚未保存，请在界面上方重试。');
          break;
        }
      }
      inFlightState = false;
    };
    retryState.current = () => { if (!disposed) void drainState(); };
    const handlePrivateMessage = (event: MessageEvent) => {
      if (disposed) return;
      const message = event.data;
      if (!message || typeof message !== 'object') return;
      if (message.type === 'ready') { clearTimeout(timer); setReady(true); post('data', { value: latestData.current }); return; }
      if (message.type === 'rendered') { if (!rendered && !hadRuntimeError) { rendered = true; latest.current.onReady?.(); } return; }
      if (message.type === 'resize') { if (typeof message.height === 'number' && Number.isFinite(message.height)) setHeight(Math.max(130, Math.min(2200, message.height))); return; }
      if (message.type === 'runtime-error') { if (typeof message.message === 'string') error(message.message); return; }
      if (message.type !== 'state' && message.type !== 'action') return;
      const current = latest.current;
      const requestId = typeof message.requestId === 'string' ? message.requestId.slice(0, 64) : '';
      if (current.readOnly || current.validationOnly) { result(requestId, 'failed', '当前是候选预览，应用后才能保存修改。'); return; }
      if (navigator.userActivation && !navigator.userActivation.isActive) { result(requestId, 'failed', '请通过界面控件主动保存，页面加载不会自动修改行程。'); return; }
      if (message.type === 'state') {
        if (!isSurfaceState(message.value) || !current.onStateChange) { result(requestId, 'failed', '交互状态无法保存。'); return; }
        queuedState = { value: JSON.parse(JSON.stringify(message.value)) as Record<string, unknown>, requestId, callback: current.onStateChange };
        void drainState();
        return;
      }
      const action = message.value;
      if (!action || typeof action !== 'object' || !current.onAction || pendingAction.current) return;
      let update: DirectUpdate;
      if (action.kind === 'note' && typeof action.value === 'string' && action.value.length <= 2000) update = { kind: 'note', targetRegionId: current.region, value: action.value };
      else if ((action.kind === 'time' || action.kind === 'replace_stop') && typeof action.stopId === 'string' && current.day.stops.some(stop => stop.id === action.stopId) && typeof action.value === 'string') {
        if (action.kind === 'time' && !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(action.value)) return;
        if (action.kind === 'replace_stop' && !current.catalog.places.some(place => place.id === action.value)) return;
        update = { kind: action.kind, targetRegionId: current.region, stopId: action.stopId, value: action.value };
      } else { result(requestId, 'failed', '这项操作超出当前日期的可保存范围。'); return; }
      pendingAction.current = true;
      Promise.resolve(current.onAction(update)).then(() => result(requestId, 'saved', '已提交保存。')).catch(() => result(requestId, 'failed', '保存失败，已有行程未被此控件覆盖。')).finally(() => { pendingAction.current = false; });
    };
    const connect = () => {
      if (disposed || privatePort.current || !frame.current?.contentWindow) return;
      const connection = new MessageChannel();
      privatePort.current = connection.port1; activePorts.add(connection.port1);
      connection.port1.addEventListener('message', handlePrivateMessage); connection.port1.start();
      frame.current.contentWindow.postMessage({ kind: SURFACE_MESSAGE, channel, type: 'connect' }, '*', [connection.port2]);
    };
    connectFrame.current = connect;
    const bootstrapReady = (event: MessageEvent) => {
      const message = event.data;
      if (event.source === frame.current?.contentWindow && message && message.kind === SURFACE_MESSAGE && message.channel === channel && message.type === 'bootstrap-ready') connect();
      // Never accept state, action, errors or resize on the public window bus.
    };
    window.addEventListener('message', bootstrapReady);
    frame.current?.contentWindow?.postMessage({ kind: SURFACE_MESSAGE, channel, type: 'probe' }, '*');
    return () => {
      disposed = true; clearTimeout(timer); window.removeEventListener('message', bootstrapReady); connectFrame.current = null; retryState.current = null;
      for (const port of activePorts) port.close();
      privatePort.current = null;
      // Do not clear queuedState: drainState owns all already received user
      // requests and completes them through the region-bound callback.
      if (queuedState && !inFlightState) void drainState();
    };
  }, [channel]);

  useEffect(() => { if (ready) privatePort.current?.postMessage({ type: 'data', value: latestData.current }); }, [ready, channel, dataKey]);

  useEffect(() => {
    const controller = new AbortController();
    setMapImage('');
    if (!needsMap) { setMapMessage(''); return () => controller.abort(); }
    if (!route?.points.some(point => point.status === 'resolved') || !['ready', 'partial'].includes(route.status)) {
      setMapMessage(route?.message || '尚未查询路线，请在主页面查询交通与地图。'); return () => controller.abort();
    }
    setMapMessage('正在载入路线地图…');
    const base = `/api/sessions/${encodeURIComponent(session.id)}`;
    const path = proposalId ? `${base}/proposals/${encodeURIComponent(proposalId)}/map/${region}?v=${session.version}` : `${base}/map/${region}?v=${session.version}`;
    void fetch(path, { credentials: 'same-origin', signal: controller.signal }).then(async response => {
      if (!response.ok || !response.headers.get('content-type')?.startsWith('image/')) throw new Error('地图图片暂时无法载入');
      const image = await response.blob(); if (image.size > 4_000_000) throw new Error('地图图片过大'); return readMapImage(image);
    }).then(value => { if (!controller.signal.aborted) { setMapImage(value); setMapIdentity(routeIdentity); setMapMessage(''); } }).catch(() => { if (!controller.signal.aborted) setMapMessage('地图暂时无法载入，景点和路线数据仍已保留。'); });
    return () => controller.abort();
  }, [routeIdentity, needsMap]);

  return <section className="generated-surface" hidden={validationOnly} data-testid={validationOnly ? 'generated-surface-check' : 'generated-surface'} data-placement={surface.placement} aria-label={surface.title || 'Agent 生成界面'}>
    <div className="generated-surface-heading"><span><Code2 size={14} />{surface.title || 'Agent 生成界面'}</span><small>{readOnly ? '交互预览 · 未保存' : '由 Agent 生成'}</small></div>
    {runtimeError && <div className="generated-surface-error" role="alert" data-testid="generated-surface-error"><AlertCircle size={16} /><div><strong>这块界面需要修复</strong><p>{runtimeError}</p><small>已有行程数据仍保留，可向 Agent 提出修复要求。</small></div></div>}
    {saveError && <div className="generated-surface-error" role="alert" data-testid="generated-surface-save-error"><AlertCircle size={16} /><div><strong>交互状态尚未保存</strong><p>{saveError}</p><button type="button" onClick={() => retryState.current?.()}>重试保存</button></div></div>}
    <iframe ref={frame} title={surface.title || 'Agent 生成旅游界面'} data-testid={validationOnly ? 'generated-surface-frame-check' : 'generated-surface-frame'} sandbox="allow-scripts" referrerPolicy="no-referrer" srcDoc={documentSource} style={{ height }} onLoad={() => connectFrame.current?.()} />
    {!ready && !runtimeError && <p className="generated-surface-loading" role="status">正在启动生成界面…</p>}
  </section>;
}
