import { useEffect, useState, type FormEvent } from 'react';
import { ArrowRight, CalendarDays, Compass, LoaderCircle, MapPin, Sparkles, Users, Wallet } from 'lucide-react';
import type { AgentStatus, Session, TripSetupRequest } from '../types';

interface Props {
  status: AgentStatus | null;
  previous: Session | null;
  busy: boolean;
  error: string;
  onSubmit: (settings: TripSetupRequest) => void;
  onContinue: () => void;
  onDemo: () => void;
  onAbort: () => void;
}
const today = () => new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
const cities = ['北京', '上海', '杭州', '成都', '西安', '南京', '广州', '厦门'];

export function TripSetup({ status, previous, busy, error, onSubmit, onContinue, onDemo, onAbort }: Props) {
  const [settings, setSettings] = useState<TripSetupRequest>({ city: '', dayCount: 3, budgetLimit: 3000, startDate: today(), travelers: 1, preferences: '' });
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!busy) { setSeconds(0); return; }
    const start = Date.now();
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [busy]);
  const update = <K extends keyof TripSetupRequest>(key: K, value: TripSetupRequest[K]) => setSettings(current => ({ ...current, [key]: value }));
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!settings.city.trim() || busy) return;
    onSubmit({ ...settings, city: settings.city.trim(), preferences: settings.preferences.trim() });
  };
  return <section className="trip-setup" data-testid="trip-setup" aria-label="规划新的旅行">
    <div className="trip-setup-intro"><span className="setup-eyebrow"><Compass size={17} />YOUR NEXT JOURNEY</span><h1>下一站，<span>由你决定。</span></h1><p>选好目的地与出行安排，让 Agent 为你规划行程。<br />从真实地点出发，再慢慢调整成喜欢的样子。</p><div className="setup-principles"><span><MapPin size={17} />真实景点与酒店</span><span><Sparkles size={17} />可修改的行程与界面</span><span><Wallet size={17} />按总预算规划</span></div>
    {previous && <div className="previous-trip" data-testid="previous-trip"><span>上次保存的旅行</span><strong>{previous.tripState.city} · {Object.keys(previous.tripState.days).length} 天</strong><p>已应用的页面和行程仍保留，可以继续使用。</p><button className="button secondary" type="button" disabled={busy} onClick={onContinue}>继续上次行程<ArrowRight size={15} /></button></div>}
    </div>
    <form className="trip-setup-form" onSubmit={submit} aria-label="行程规划参数">
      <div className="setup-form-heading"><span className="panel-eyebrow">START PLANNING</span><h2>先说说你的旅行</h2><p>预算为所有同行人的全程总预算，实际费用仍需确认。</p></div>
      <fieldset disabled={busy}><div className="setup-field"><label htmlFor="setup-city"><MapPin size={15} />目的地城市</label><input id="setup-city" name="city" data-testid="setup-city" autoComplete="off" required maxLength={60} list="travel-cities" placeholder="输入城市，例如成都、上海" value={settings.city} onChange={event => update('city', event.target.value)} /><datalist id="travel-cities">{cities.map(city => <option value={city} key={city} />)}</datalist><div className="setup-city-chips" aria-label="常见目的地">{cities.slice(0, 6).map(city => <button key={city} type="button" onClick={() => update('city', city)} aria-pressed={settings.city === city}>{city}</button>)}</div></div>
      <div className="setup-field-grid"><div className="setup-field"><label htmlFor="setup-start-date"><CalendarDays size={15} />出发日期</label><input id="setup-start-date" name="startDate" type="date" required value={settings.startDate} onChange={event => update('startDate', event.target.value)} /></div><div className="setup-field"><label htmlFor="setup-day-count">旅游天数</label><input id="setup-day-count" name="dayCount" type="number" inputMode="numeric" min={1} max={14} step={1} required value={settings.dayCount || ''} onChange={event => update('dayCount', Number(event.target.value))} /><small>支持 1–14 天</small></div><div className="setup-field"><label htmlFor="setup-budget"><Wallet size={15} />全程总预算（元）</label><input id="setup-budget" name="budgetLimit" type="number" inputMode="numeric" min={100} max={100000} step={1} required value={settings.budgetLimit || ''} onChange={event => update('budgetLimit', Number(event.target.value))} /></div><div className="setup-field"><label htmlFor="setup-travelers"><Users size={15} />同行人数</label><input id="setup-travelers" name="travelers" type="number" inputMode="numeric" min={1} max={10} step={1} required value={settings.travelers || ''} onChange={event => update('travelers', Number(event.target.value))} /></div></div>
      <div className="setup-field"><label htmlFor="setup-preferences">偏好与补充要求 <span>选填</span></label><textarea id="setup-preferences" name="preferences" rows={3} maxLength={1000} placeholder="例如：喜欢历史与美食，节奏轻松，少爬山" value={settings.preferences} onChange={event => update('preferences', event.target.value)} /></div></fieldset>
      {error && <p className="setup-error" role="alert">{error}</p>}
      {status?.mode === 'llm' && !status.configured && <p className="setup-service-note">模型服务尚未配置，请由维护者在后台填写 API 配置后再规划。</p>}
      <button type="submit" className="button primary setup-submit" disabled={busy || !settings.city.trim()} data-testid="plan-trip">{busy ? <LoaderCircle className="spin" size={17} /> : <Sparkles size={17} />}{busy ? `正在规划行程 · ${seconds} 秒` : '开始规划行程'}<ArrowRight size={16} /></button>
      {busy && <div className="setup-planning-progress" role="status"><p>正在查找真实地点并安排每日行程，请稍候。</p><button type="button" onClick={onAbort}>停止等待</button></div>}
      <button type="button" className="button secondary setup-demo-button" onClick={onDemo} disabled={busy} data-testid="open-three-day-demo">打开三日演示（不调用模型）</button>
      <p className="setup-footnote">支持地图服务覆盖的中国城市。景点与酒店来自真实地点信息，门票、交通和住宿金额为预算估算；实时价格与可预订情况以服务方为准。</p>
    </form>
  </section>;
}
