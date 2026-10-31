import type { ReactNode } from 'react';
import { ArrowUpRight, Clock3, MapPin, Mountain, Umbrella } from 'lucide-react';
import type { Place, TripStop } from '../types';

const money = (value: number) => `¥${value.toLocaleString('zh-CN')}`;
function officialLink(value?: string) {
  if (!value) return undefined;
  try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? url.href : undefined; }
  catch { return undefined; }
}

// Shared trusted data-bound content for the original card and isolated previews.
// readOnly never renders outbound links, editable controls, map images or effects.
export function StopCardContent({ stop, place, showCost, compact, readOnly = false, children }: {
  stop: TripStop; place?: Place; showCost: boolean; compact: boolean; readOnly?: boolean; children?: ReactNode;
}) {
  const source = readOnly ? undefined : officialLink(place?.sourceUrl);
  return <div className="stop-content">
    <div className="stop-title-row"><div className="place-symbol">{place?.indoor ? <Umbrella size={19} strokeWidth={1.6} /> : <Mountain size={19} strokeWidth={1.6} />}</div><h4>{place?.name ?? '未找到景点'}</h4>{showCost && <span className="stop-cost">门票预算 {money(place?.cost ?? 0)}</span>}</div>
    {!compact && <p className="stop-description">{stop.description ?? place?.description ?? '请刷新以重新载入景点信息。'}</p>}
    <div className="stop-meta"><span><Clock3 size={12} />{place ? `${place.durationMinutes} 分钟` : '时长未知'}</span><span><MapPin size={12} />{place?.location ?? '—'}</span><span className={`place-tag ${place?.indoor ? 'is-indoor' : ''}`}>{place?.indoor ? '室内' : '户外'}</span></div>
    {place?.address && <p className="place-address"><MapPin size={12} /><span>{place.address}</span></p>}
    {!readOnly && children}
    {source && <a className="place-source-link" href={source} target="_blank" rel="noopener noreferrer">{place?.sourceName || '官方地点信息'}<ArrowUpRight size={12} /></a>}
  </div>;
}
