import { readPreviewStream } from './features/agentic-editor/protocol';
import type { DisplayChange, VersionDiff, VersionList, VersionRecord } from './features/agentic-editor/historyTypes';
import { parseCandidateBatch } from './features/agentic-editor/candidateSchema';
import type { CandidateAgentMode, CandidateAgentStatus } from './features/agentic-editor/candidateTypes';
import type { TripDocument } from './features/agentic-editor/types';
import { parseTripDocument } from './features/agentic-editor/tripSchema';
import type { EditorField, EditorScope, PatchPreview, ReplaceOperation } from './features/agentic-editor/types';
import type { AgentStatus, Catalog, DirectUpdate, IntegrationStatus, ProposalBatch, RegionId, Session, TargetRegion, TripSetupRequest } from './types';

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, options: RequestInit = {}, timeoutMs = 20000): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  if (options.signal?.aborted) controller.abort();
  options.signal?.addEventListener('abort', abort, { once: true });
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  try {
    const response = await fetch(`/api${path}`, {
      ...options,
      signal: controller.signal,
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', ...options.headers },
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null) as { detail?: string | { code?: string; message?: string } } | null;
      throw new ApiError(response.status, typeof body?.detail === 'string' ? body.detail : body?.detail?.message || `请求失败（${response.status}），请稍后重试。`);
    }
    const body: unknown = await response.json();
    try {
      if (body && typeof body === 'object') {
        const record = body as Record<string, unknown>;
        if (record.tripDocument !== undefined) record.tripDocument = parseTripDocument(record.tripDocument);
        if (record.document !== undefined) record.document = parseTripDocument(record.document);
      }
    } catch {
      throw new ApiError(502, '服务端返回的结构化行程未通过校验；本次结果未被载入，请重新获取。');
    }
    return body as T;
  } catch (error) {
    if (timedOut) throw new ApiError(408, '本次处理时间较长，已停止等待。行程未被自动修改，请稍后重新预览。');
    if (error instanceof ApiError || (error instanceof DOMException && error.name === 'AbortError')) throw error;
    throw new ApiError(0, '暂时无法连接服务，请确认后端已启动后重试。');
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener('abort', abort);
  }
}

const post = <T>(path: string, body: unknown, signal?: AbortSignal, timeoutMs?: number) => request<T>(path, { method: 'POST', body: JSON.stringify(body), signal }, timeoutMs);
const patch = <T>(path: string, body: unknown, signal?: AbortSignal, timeoutMs?: number) => request<T>(path, { method: 'PATCH', body: JSON.stringify(body), signal }, timeoutMs);
const put = <T>(path: string, body: unknown, signal?: AbortSignal, timeoutMs?: number) => request<T>(path, { method: 'PUT', body: JSON.stringify(body), signal }, timeoutMs);
const remove = <T>(path: string, signal?: AbortSignal) => request<T>(path, { method: 'DELETE', body: JSON.stringify({}), signal });
const route = (id: string) => `/sessions/${encodeURIComponent(id)}`;

export const api = {
  versions: (id: string, signal?: AbortSignal) => request<VersionList>(route(id) + '/versions', { signal }),
  version: (id: string, version: number, signal?: AbortSignal) => request<VersionRecord>(route(id) + '/versions/' + version, { signal }),
  versionDiff: (id: string, version: number, againstVersion: number, signal?: AbortSignal) => request<VersionDiff>(route(id) + '/versions/' + version + '/diff?againstVersion=' + againstVersion, { signal }),
  restoreVersion: (id: string, baseVersion: number, targetVersion: number, signal?: AbortSignal) => post<Session>(route(id) + '/versions/restore', { baseVersion, targetVersion, confirmRestore: true }, signal),
  editorDisplay: (id: string, baseVersion: number, selectedComponentId: string, change: DisplayChange, signal?: AbortSignal) => post<Session>(route(id) + '/editor/display', { baseVersion, selectedComponentId, ...change }, signal),
  globalPresentation: (id: string, baseVersion: number, showBudgetDetails: boolean, signal?: AbortSignal) => post<Session>(route(id) + '/editor/global-presentation', { baseVersion, confirmGlobal: true, showBudgetDetails }, signal),
  budgetConstraint: (id: string, baseVersion: number, maxBudget: number | null, signal?: AbortSignal) => post<Session>(route(id) + '/editor/constraints', { baseVersion, confirmConstraintChange: true, maxBudget }, signal),
  candidateStatus: (signal?: AbortSignal) => request<CandidateAgentStatus>('/edits/status', { signal }),
  editCandidates: async (id: string, current: TripDocument, selectedComponentIds: string[], instruction: string, agentMode: CandidateAgentMode, fields: EditorField[], signal?: AbortSignal, beforeSession?: Session, onProgress?: (name:string,value?:unknown)=>void) => {
    const body = { baseVersion:current.version,selectedComponentIds,instruction,agentMode,fields };
    let result:unknown;
    if(onProgress){
      const controller=new AbortController();const abort=()=>controller.abort();signal?.addEventListener('abort',abort,{once:true});if(signal?.aborted)controller.abort();
      const timer=setTimeout(abort,180000);
      try{
        const response=await fetch('/api'+route(id)+'/edits/candidates',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','Accept':'text/event-stream'},body:JSON.stringify(body),signal:controller.signal});
        if(!response.ok){const error=await response.json().catch(()=>null);throw new ApiError(response.status,typeof error?.detail==='string'?error.detail:error?.detail?.message||'生成失败，请重试。');}
        result=await readPreviewStream(response,id,onProgress);
      }catch(error){if(controller.signal.aborted&&!signal?.aborted)throw new ApiError(408,'生成等待超时，正式行程未修改，请重新生成。');throw error;}finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);}
    }else result=await post<unknown>(route(id)+'/edits/candidates',body,signal,180000);
    try { const batch=parseCandidateBatch(result, current, beforeSession); if(onProgress&&batch.candidates.some(c=>!c.a2ui||!c.evaluation))throw Error('流式候选缺少协议配置或审核评分'); return batch; }
    catch { throw new ApiError(502, '候选结构或实际差异未通过前端校验；正式行程未修改，请重新生成。'); }
  },
  commitEditCandidate: (id: string, baseVersion: number, candidateId: string, signal?: AbortSignal) => post<Session>(route(id) + '/edits/commit', { baseVersion, candidateId }, signal),
  createDemoSession: (settings: TripSetupRequest, signal?: AbortSignal) => post<Session>('/demo-sessions', settings, signal, 120000),
  editorScope: (id: string, baseVersion: number, selectedComponentId: string, fields: EditorField[], signal?: AbortSignal) => post<EditorScope>(route(id) + '/editor/scope', { baseVersion, selectedComponentId, fields }, signal),
  editorLock: (id: string, baseVersion: number, componentId: string, locked: boolean, signal?: AbortSignal) => patch<Session>(route(id) + '/editor/locks', { baseVersion, componentId, locked }, signal),
  editorPreview: (id: string, baseVersion: number, selectedComponentId: string, fields: EditorField[], patch: ReplaceOperation[], signal?: AbortSignal) => post<PatchPreview>(route(id) + '/editor/preview', { baseVersion, selectedComponentId, fields, patch }, signal),
  editorApply: (id: string, baseVersion: number, previewId: string, signal?: AbortSignal) => post<Session>(route(id) + '/editor/apply', { baseVersion, previewId }, signal),
  presentation: (id: string, baseVersion: number, targetRegionId: RegionId, view: 'cards' | 'timeline', signal?: AbortSignal) => post<Session>(route(id) + '/editor/presentation', { baseVersion, targetRegionId, view }, signal),
  agentStatus: (signal?: AbortSignal) => request<AgentStatus>('/agent/status', { signal }),
  integrations: (signal?: AbortSignal) => request<IntegrationStatus>('/integrations', { signal }),
  configureModel: (value: { apiKey?: string; baseUrl: string; model?: string }, signal?: AbortSignal) => put<IntegrationStatus>('/integrations/model', value, signal),
  clearModel: (signal?: AbortSignal) => remove<IntegrationStatus>('/integrations/model', signal),
  configureAmap: (value: { apiKey?: string; baseUrl: string }, signal?: AbortSignal) => put<IntegrationStatus>('/integrations/amap', value, signal),
  clearAmap: (signal?: AbortSignal) => remove<IntegrationStatus>('/integrations/amap', signal),
  catalog: (signal?: AbortSignal) => request<Catalog>('/catalog', { signal }),
  createSession: (settings: TripSetupRequest, signal?: AbortSignal) => post<Session>('/sessions', settings, signal, 300000),
  session: (id: string, signal?: AbortSignal) => request<Session>(route(id), { signal }),
  generate: (id: string, baseVersion: number, targetRegionId: TargetRegion, instruction: string, generateCandidates: boolean, signal?: AbortSignal) => post<ProposalBatch>(`${route(id)}/generate`, { baseVersion, targetRegionId, instruction, generateCandidates }, signal, 300000),
  repair: (id: string, baseVersion: number, proposalId: string, error: string, signal?: AbortSignal) => post<ProposalBatch>(`${route(id)}/proposals/${encodeURIComponent(proposalId)}/repair`, { baseVersion, error: error.slice(0, 2000) }, signal, 300000),
  refreshRoutes: (id: string, baseVersion: number, targetRegionId: TargetRegion, signal?: AbortSignal) => post<Session>(`${route(id)}/routes`, { baseVersion, targetRegionId }, signal, 120000),
  apply: (id: string, baseVersion: number, proposalId: string, signal?: AbortSignal) => post<Session>(`${route(id)}/apply`, { baseVersion, proposalId }, signal),
  lock: (id: string, baseVersion: number, target: RegionId | 'hotel', locked: boolean, signal?: AbortSignal) => patch<Session>(`${route(id)}/locks`, { baseVersion, target, locked }, signal),
  direct: (id: string, baseVersion: number, update: DirectUpdate, signal?: AbortSignal) => patch<Session>(`${route(id)}/direct`, { baseVersion, ...update }, signal, update.kind === 'replace_stop' || update.kind === 'time' ? 120000 : 20000),
  undo: (id: string, baseVersion: number, signal?: AbortSignal) => post<Session>(`${route(id)}/undo`, { baseVersion }, signal),
  reset: (id: string, baseVersion: number, signal?: AbortSignal) => post<Session>(`${route(id)}/reset`, { baseVersion }, signal),
};
