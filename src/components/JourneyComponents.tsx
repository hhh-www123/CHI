import { useEffect, useState } from 'react';
import { AlertCircle, ArrowUpRight, Bus, Car, CheckCircle2, Footprints, Map, MapPin } from 'lucide-react';
import type { ContentBlock, RegionId, RouteDay, RouteSegment } from '../types';

const modeNames = { transit: '公共交通', driving: '驾车', walking: '步行' };

function mapLink(value: string | null | undefined) {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && (url.hostname === 'amap.com' || url.hostname.endsWith('.amap.com')) ? url.href : undefined;
  } catch { return undefined; }
}

export function TransportLeg({ segment, fromName, toName, mode, message }: {
  segment?: RouteSegment;
  fromName: string;
  toName: string;
  mode: 'transit' | 'driving' | 'walking';
  message?: string;
}) {
  const selectedMode = segment?.mode ?? mode;
  const Icon = selectedMode === 'driving' ? Car : selectedMode === 'walking' ? Footprints : Bus;
  const ready = segment?.status === 'ready';
  const link = mapLink(segment?.navigationUrl);
  return <aside className={`transport-leg ${ready ? 'is-ready' : 'is-unavailable'}`} aria-label={`${fromName}到${toName}交通`}>
    <span className="transport-leg-icon"><Icon size={16} /></span>
    <div className="transport-leg-body">
      <div className="transport-leg-heading"><strong>{modeNames[selectedMode]}</strong>
        {ready && segment?.durationMinutes != null && <span>约 {Math.ceil(segment.durationMinutes)} 分钟</span>}
        {ready && segment?.distanceMeters != null && <span>{segment.distanceMeters >= 1000 ? `${(segment.distanceMeters / 1000).toFixed(1)} 公里` : `${Math.round(segment.distanceMeters)} 米`}</span>}
      </div>
      <p>{ready ? segment?.summary : segment?.summary || message || '请点击“刷新交通与地图”查询当前景点之间的路线，时长与费用尚未确认。'}</p>
      <small>{fromName} → {toName}</small>
    </div>
    {link && <a className="transport-open" href={link} target="_blank" rel="noopener noreferrer" aria-label={`打开${fromName}到${toName}的地图路线`}><ArrowUpRight size={15} /></a>}
  </aside>;
}

export function DayRouteMap({ sessionId, version, region, dayName, route, proposalId }: {
  sessionId: string;
  version: number;
  region: RegionId;
  dayName: string;
  route?: RouteDay;
  proposalId?: string;
}) {
  const [imageFailed, setImageFailed] = useState(false);
  useEffect(() => setImageFailed(false), [sessionId, version, region, proposalId]);
  const hasResolved = route?.points.some(point => point.status === 'resolved');
  const canShowImage = hasResolved && (route?.status === 'ready' || route?.status === 'partial') && !imageFailed;
  return <section className="route-map-section" aria-label={`${dayName}路线地图`}>
    <div className="route-section-heading"><div><span className="mini-eyebrow">ON THE MAP</span><h4><Map size={17} />把今天放进地图</h4></div><span className="map-source">高德地图</span></div>
    {canShowImage ? <figure className="route-map-figure">
      <img src={proposalId ? `/api/sessions/${encodeURIComponent(sessionId)}/proposals/${encodeURIComponent(proposalId)}/map/${region}?v=${version}` : `/api/sessions/${encodeURIComponent(sessionId)}/map/${region}?v=${version}`} alt={`${dayName}景点与已查询路线地图`} onError={() => setImageFailed(true)} loading="lazy" />
      <figcaption>路线来自地图查询；未查到的路段不会连线。点击下方地点可在地图中查看。</figcaption>
    </figure> : <div className="map-unavailable"><MapPin size={24} /><strong>{imageFailed ? '地图暂时未能载入' : '地图位置待确认'}</strong><p>{imageFailed ? '已有行程仍已保留，可以通过下方地点链接查看地图。' : route?.message || '请点击“添加交通与地图”或“刷新交通与地图”，查询当前景点的位置和路线。'}</p></div>}
    {!!route?.points.length && <ol className="map-point-list">{route.points.map((point, index) => {
      const link = mapLink(point.searchUrl);
      return <li key={point.stopId}><span className="map-point-number">{index + 1}</span><div><strong>{point.name}</strong><small>{point.status === 'resolved' ? '已匹配地图位置' : point.status === 'fictional' ? '旧地点尚待更新，暂不可导航' : '位置未确认'}</small></div>{link && point.status !== 'fictional' && <a href={link} target="_blank" rel="noopener noreferrer" aria-label={`在地图中查看${point.name}`}>查看<ArrowUpRight size={13} /></a>}</li>;
    })}</ol>}
    {route?.queriedAt && <p className="route-query-time">{route.status === 'unconfigured' ? '检查于' : '查询于'} {new Date(route.queriedAt).toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' })} · 路况与班次以出行时为准</p>}
  </section>;
}

export function GeneratedBlocks({ blocks }: { blocks?: ContentBlock[] }) {
  if (!blocks?.length) return null;
  return <section className="generated-blocks" aria-label="新增行程内容">{blocks.map(block => <article className={`generated-block generated-block--${block.type}`} key={block.id}>
    <h4><CheckCircle2 size={16} />{block.title}</h4>
    {!!block.text && <p>{block.text}</p>}
    {block.type === 'checklist' && <ul className="generated-checklist">{block.items.map((item, index) => <li key={`${block.id}-${index}`}><span aria-hidden="true">○</span>{item}</li>)}</ul>}
    {block.type === 'table' && <div className="table-scroll"><table className="generated-table"><caption className="sr-only">{block.title}</caption><thead><tr>{block.columns.map((column, index) => <th key={index} scope="col">{column}</th>)}</tr></thead><tbody>{block.rows.map((row, index) => <tr key={index}>{block.columns.map((_, columnIndex) => <td key={columnIndex}>{row[columnIndex] ?? '—'}</td>)}</tr>)}</tbody></table></div>}
    {block.type !== 'note' && block.type !== 'checklist' && block.type !== 'table' && <p className="inline-warning"><AlertCircle size={14} />此内容暂时无法展示，请重新生成。</p>}
  </article>)}</section>;
}
