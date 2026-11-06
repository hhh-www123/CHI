import { ArrowLeft, ArrowUpRight, BookOpen, Clock3, MapPin, ShieldCheck, Wallet } from 'lucide-react';
import type { Catalog } from '../types';

const money = (value: number) => `¥${value.toLocaleString('zh-CN')}`;
const safeLink = (value?: string) => {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : undefined;
  } catch { return undefined; }
};

export function GuidePage({ catalog, city, onBack }: { catalog: Catalog; city?: string; onBack: () => void }) {
  const routes = catalog.curatedRoutes ?? [];
  const recommendations = catalog.guideRecommendations ?? [];
  const placeById = (id: string) => catalog.places.find(place => place.id === id);
  return <section className="guide-page" data-testid="guide-page" aria-labelledby="guide-page-title">
    <header className="guide-page-hero">
      <div><span className="section-eyebrow"><BookOpen size={14} />TRAVEL FIELD NOTES</span><h1 id="guide-page-title">{city || catalog.city || '旅行'}指南</h1><p>按主题与片区查看路线灵感，再回到行程规划中调整安排。</p></div>
      <button type="button" className="button secondary" onClick={onBack}><ArrowLeft size={15} />返回行程规划</button>
    </header>
    {routes.length || recommendations.length ? <>
      {!!routes.length && <section className="guide-page-section" aria-labelledby="guide-routes-title"><div className="guide-section-heading"><span><span className="section-eyebrow">CURATED ROUTES</span><h2 id="guide-routes-title">按片区慢慢打开</h2></span>{safeLink(catalog.guideSource?.url) && <a href={safeLink(catalog.guideSource?.url)} target="_blank" rel="noopener noreferrer">查看参考指南<ArrowUpRight size={13} /></a>}</div><div className="hangzhou-route-grid">{routes.map(route => <article className="hangzhou-route-card" key={route.id}><span>{route.theme}</span><h3>{route.title}</h3><p>{route.summary}</p><div className="hangzhou-route-stops">{route.placeIds.map(placeId => <em key={placeId}>{placeById(placeId)?.name ?? placeId}</em>)}</div><div className="hangzhou-route-meta"><span><Clock3 size={13} />约 {Math.round(route.durationMinutes / 30) / 2} 小时</span><strong><Wallet size={13} />人均约 {money(route.budgetPerPerson.total)}</strong></div><small>门票 {money(route.budgetPerPerson.admission)} · 交通 {money(route.budgetPerPerson.transport)} · 餐饮 {money(route.budgetPerPerson.food)}{route.budgetPerPerson.optional > 0 ? ` · 可选项目 ${money(route.budgetPerPerson.optional)}` : ''}</small></article>)}</div></section>}
      {!!recommendations.length && <section className="guide-page-section" aria-labelledby="guide-inspiration-title"><div className="guide-section-heading"><span><span className="section-eyebrow">MORE IDEAS</span><h2 id="guide-inspiration-title">更多去处</h2></span></div><div className="guide-recommendation-grid">{recommendations.map(item => <article key={item.id}><div><span>{item.category}</span><strong>{item.name}</strong></div><p>{item.description}</p><div className="guide-recommendation-meta"><span><MapPin size={12} />{item.location}</span><span><Clock3 size={12} />{item.durationMinutes} 分钟</span><span className="guide-selectable"><ShieldCheck size={11} />可在行程中选择</span><b>{item.estimatedCost > 0 ? `参考 ${money(item.estimatedCost)}` : '基础游览免费'}</b></div><small>{item.priceNote}</small></article>)}</div><p className="guide-verification-note"><ShieldCheck size={13} />指南中的地点都已加入行程候选，可通过“换个景点”选择，也可交给 Agent 调整。</p></section>}
    </> : <div className="guide-empty"><BookOpen size={28} /><h2>当前城市暂未整理专题指南</h2><p>可以先返回行程规划，指南内容后续会按城市继续扩充。</p></div>}
  </section>;
}
