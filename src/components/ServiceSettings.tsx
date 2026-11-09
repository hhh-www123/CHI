import { KeyRound, LoaderCircle, MapPinned, PlugZap, ShieldCheck, Trash2, X } from 'lucide-react';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api } from '../api';
import type { IntegrationStatus } from '../types';

type Target = 'model' | 'amap';

export function ServiceSettings({ target = 'model', onClose, onChanged }: {
  target?: Target;
  onClose: () => void;
  onChanged: (status: IntegrationStatus) => void | Promise<void>;
}) {
  const [activeTarget, setActiveTarget] = useState<Target>(target);
  const [status, setStatus] = useState<IntegrationStatus | null>(null);
  const [modelKey, setModelKey] = useState('');
  const [modelUrl, setModelUrl] = useState('https://api.deepseek.com');
  const [modelId, setModelId] = useState('');
  const [amapKey, setAmapKey] = useState('');
  const [pending, setPending] = useState<Target | 'clear-model' | 'clear-amap' | ''>('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const modelInput = useRef<HTMLInputElement>(null);
  const amapInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    setActiveTarget(target);
    api.integrations(controller.signal).then(next => {
      if (controller.signal.aborted) return;
      setStatus(next); setModelUrl(next.model.baseUrl); setModelId(next.model.model);
      requestAnimationFrame(() => (target === 'amap' ? amapInput.current : modelInput.current)?.focus());
    }).catch(value => { if (!controller.signal.aborted) setError(value instanceof Error ? value.message : '无法读取服务接入状态。'); });
    return () => controller.abort();
  }, [target]);

  async function saveModel(event: FormEvent) {
    event.preventDefault(); setError(''); setMessage(''); setPending('model');
    try {
      const next = await api.configureModel({ apiKey: modelKey.trim() || undefined, baseUrl: modelUrl.trim(), model: modelId.trim() || undefined });
      setStatus(next); setModelKey(''); setModelUrl(next.model.baseUrl); setModelId(next.model.model);
      setMessage('模型服务已接入，后续规划和 Agent 对话将使用这项配置。'); await onChanged(next);
    } catch (value) { setError(value instanceof Error ? value.message : '模型服务保存失败。'); }
    finally { setPending(''); }
  }
  async function saveAmap(event: FormEvent) {
    event.preventDefault(); setError(''); setMessage(''); setPending('amap');
    try {
      const next = await api.configureAmap({ apiKey: amapKey.trim() || undefined, baseUrl: status?.amap.baseUrl || 'https://restapi.amap.com' });
      setStatus(next); setAmapKey(''); setMessage('高德地图服务已接入，可以查询地点、交通和地图。'); await onChanged(next);
    } catch (value) { setError(value instanceof Error ? value.message : '高德地图服务保存失败。'); }
    finally { setPending(''); }
  }
  async function clear(targetName: Target) {
    setError(''); setMessage(''); setPending(targetName === 'model' ? 'clear-model' : 'clear-amap');
    try {
      const next = targetName === 'model' ? await api.clearModel() : await api.clearAmap();
      setStatus(next); setModelKey(''); setAmapKey(''); setMessage(targetName === 'model' ? '模型服务接入已移除。' : '高德地图接入已移除。'); await onChanged(next);
    } catch (value) { setError(value instanceof Error ? value.message : '无法移除服务接入。'); }
    finally { setPending(''); }
  }

  return <>
    <header className="service-settings-header"><div><span className="dialog-icon"><PlugZap size={22} /></span><div><span className="panel-eyebrow">SERVICE CONNECTIONS</span><h2>服务接入</h2></div></div><button type="button" className="icon-button" aria-label="关闭服务接入" onClick={onClose} disabled={!!pending}><X size={19} /></button></header>
    <p className="service-settings-intro">在本机填写接口信息。API Key 不会回显，也不会进入行程数据或 Git。</p>
    <div className="service-settings-grid">
      <form className={`integration-card ${activeTarget === 'model' ? 'is-target' : ''}`} onSubmit={saveModel} onClick={() => setActiveTarget('model')} onFocusCapture={() => setActiveTarget('model')} data-testid="model-integration-form" data-active={activeTarget === 'model' ? 'true' : 'false'}>
        <div className="integration-heading"><span><KeyRound size={17} /></span><div><h3>模型 API</h3><p>OpenAI Chat Completions 兼容接口</p></div><em className={status?.model.configured ? 'is-connected' : ''}>{status?.model.configured ? '已接入' : '未接入'}</em></div>
        <label htmlFor="integration-model-key">API Key</label><input ref={modelInput} id="integration-model-key" type="password" autoComplete="new-password" spellCheck={false} maxLength={4096} value={modelKey} onChange={event => setModelKey(event.target.value)} placeholder={status?.model.configured ? '留空则保留当前 Key' : '输入 API Key'} />
        <label htmlFor="integration-model-url">请求地址</label><input id="integration-model-url" type="url" inputMode="url" spellCheck={false} required value={modelUrl} onChange={event => setModelUrl(event.target.value)} placeholder="https://api.example.com/v1" />
        <label htmlFor="integration-model-id">模型 ID <small>可选，默认沿用当前值</small></label><input id="integration-model-id" type="text" spellCheck={false} maxLength={200} value={modelId} onChange={event => setModelId(event.target.value)} placeholder="例如 deepseek-chat" />
        <p className="integration-hint">可填写基础地址，也可粘贴以 <code>/chat/completions</code> 结尾的完整地址，服务端会自动规范化。</p>
        <div className="integration-actions"><button className="button primary" type="submit" disabled={!!pending || !modelUrl.trim()}>{pending === 'model' ? <LoaderCircle className="spin" size={15} /> : <PlugZap size={15} />}{pending === 'model' ? '正在保存…' : '保存模型接入'}</button>{status?.model.configured && <button className="button ghost danger" type="button" disabled={!!pending} onClick={() => void clear('model')}><Trash2 size={14} />移除</button>}</div>
      </form>
      <form className={`integration-card ${activeTarget === 'amap' ? 'is-target' : ''}`} onSubmit={saveAmap} onClick={() => setActiveTarget('amap')} onFocusCapture={() => setActiveTarget('amap')} data-testid="amap-integration-form" data-active={activeTarget === 'amap' ? 'true' : 'false'}>
        <div className="integration-heading"><span><MapPinned size={17} /></span><div><h3>高德地图 API</h3><p>地点、路线与静态地图</p></div><em className={status?.amap.configured ? 'is-connected' : ''}>{status?.amap.configured ? '已接入' : '未接入'}</em></div>
        <label htmlFor="integration-amap-key">Web 服务 Key</label><input ref={amapInput} id="integration-amap-key" type="password" autoComplete="new-password" spellCheck={false} maxLength={4096} value={amapKey} onChange={event => setAmapKey(event.target.value)} placeholder={status?.amap.configured ? '留空则保留当前 Key' : '输入高德 Web 服务 Key'} />
        <label htmlFor="integration-amap-url">请求地址 <small>官方固定地址</small></label><input id="integration-amap-url" type="url" readOnly value={status?.amap.baseUrl || 'https://restapi.amap.com'} />
        <p className="integration-hint">请使用高德开放平台的“Web 服务”Key；浏览器不会直接持有该 Key。</p>
        <div className="integration-actions"><button className="button primary" type="submit" disabled={!!pending}>{pending === 'amap' ? <LoaderCircle className="spin" size={15} /> : <MapPinned size={15} />}{pending === 'amap' ? '正在保存…' : '保存高德接入'}</button>{status?.amap.configured && <button className="button ghost danger" type="button" disabled={!!pending} onClick={() => void clear('amap')}><Trash2 size={14} />移除</button>}</div>
      </form>
    </div>
    {error && <p className="service-settings-message is-error" role="alert">{error}</p>}
    {message && <p className="service-settings-message" role="status">{message}</p>}
    <p className="service-settings-safety"><ShieldCheck size={14} />设置保存在本机服务的运行目录中，并被仓库忽略；公开部署时此入口只接受本机访问。</p>
  </>;
}
