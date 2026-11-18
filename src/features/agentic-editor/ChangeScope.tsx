import type { DayKey, Session } from '../../types';
import { sessionDayItems } from '../../types';
import { isComponentLocked } from './LocalEditor';
import type { ComponentBinding, EditorScope } from './types';
import type { EditCandidate } from './candidateTypes';

export interface CandidateVisualization {
  baseVersion: number;
  scope: EditorScope;
  candidate: EditCandidate | null;
}
const fieldLabels: Record<string, string> = {
  description: '描述',
  startTime: '开始时间',
  placeId: '景点',
  title: '名称',
  period: '时段',
  parentId: '分组',
};
const periodLabels: Record<string, string> = {
  am: '上午',
  pm: '下午',
  evening: '晚上',
};

function displayValue(field: string, value: string, session: Session) {
  if (!value) return '（空）';
  if (field === 'placeId')
    return (
      session.catalog?.places.find((item) => item.id === value)?.name ?? value
    );
  if (field === 'period') return periodLabels[value] ?? value;
  if (field === 'parentId') {
    const label = periodLabels[value.split('-').at(-1) ?? ''];
    return label ? `${label}分组` : value;
  }
  return value;
}

export function scopeState(
  session: Session,
  binding: ComponentBinding | undefined,
  visual: CandidateVisualization | null,
) {
  if (!binding || !visual || visual.baseVersion !== session.version)
    return undefined;
  if (isComponentLocked(session, binding.componentId)) return 'locked';
  if (
    visual.candidate?.differences.some(
      (item) => item.activityId === binding.entityId,
    )
  )
    return 'changed';
  return visual.scope.eligibleActivityIds.includes(binding.entityId)
    ? 'allowed'
    : 'protected';
}

export function ScopeChangeMap({
  session,
  visual,
  onDay,
}: {
  session: Session;
  visual: CandidateVisualization | null;
  onDay: (day: DayKey) => void;
}) {
  if (!visual || visual.baseVersion !== session.version) return null;
  const registry = Object.values(session.componentRegistry ?? {});
  const fields = visual.scope.editableFields
    .map((field) => fieldLabels[field])
    .join('、');
  return (
    <section
      className="scope-change-map"
      aria-label="修改范围可视化"
      data-testid="scope-change-map"
    >
      <div className="governance-item-heading">
        <strong>{visual.candidate ? '候选变化地图' : '本次授权范围'}</strong>
        <span>基于 v{visual.baseVersion}</span>
      </div>
      <p>
        {visual.scope.label} · {fields || '无获准字段'}
        {visual.candidate &&
          ` · ${visual.candidate.differences.length} 个字段变化`}
      </p>
      <div className="scope-day-map">
        {sessionDayItems(session).map((day) => {
          const bindings = registry.filter(
            (item) => item.dayKey === day.key && item.kind === 'ActivityCard',
          );
          const changed =
            visual.candidate?.differences.filter((item) =>
              bindings.some((binding) => binding.entityId === item.activityId),
            ).length ?? 0;
          const allowed = bindings.filter((item) =>
            visual.scope.eligibleActivityIds.includes(item.entityId),
          ).length;
          const dayBinding = registry.find(
            (item) => item.dayKey === day.key && item.kind === 'DayCard',
          );
          const locked = isComponentLocked(session, dayBinding?.componentId);
          let state = 'protected';
          let label = '选区外保护';
          if (locked) {
            state = 'locked';
            label = '已锁定';
          } else if (changed) {
            state = 'changed';
            label = `${changed} 项候选变化`;
          } else if (allowed) {
            state = 'allowed';
            label = `${allowed} 个景点可改`;
          }
          return (
            <button
              type="button"
              key={day.key}
              data-scope-state={state}
              onClick={() => onDay(day.key)}
              aria-label={`查看${day.label}修改范围`}
            >
              <strong>{day.label}</strong>
              <span>{label}</span>
            </button>
          );
        })}
      </div>
      <div className="scope-map-legend">
        <span data-scope-state="allowed">授权可改</span>
        <span data-scope-state="changed">候选变化</span>
        <span data-scope-state="locked">锁定保护</span>
        <span>酒店与预算保持</span>
      </div>
    </section>
  );
}

export function CardChangeSummary({
  session,
  binding,
  visual,
}: {
  session: Session;
  binding: ComponentBinding | undefined;
  visual: CandidateVisualization | null;
}) {
  const state = scopeState(session, binding, visual);
  if (!state || !binding || !visual) return null;
  const differences =
    visual.candidate?.differences.filter(
      (item) => item.activityId === binding.entityId,
    ) ?? [];
  if (!differences.length)
    return (
      <div className={`card-scope-label is-${state}`}>
        {state === 'allowed'
          ? '本次授权可改'
          : state === 'locked'
            ? '锁定保护'
            : '选区外保护'}
      </div>
    );
  return (
    <section
      className="card-change-summary"
      aria-label={`${binding.label}候选变化`}
    >
      <strong>候选变化 · 尚待采用</strong>
      {differences.map((item) => (
        <div key={item.path}>
          <span>
            {fieldLabels[item.field]}
            {visual.candidate?.derivedChangedPaths.includes(item.path)
              ? ' · 联动同步'
              : ' · 直接修改'}
          </span>
          <del>{displayValue(item.field, item.before, session)}</del>
          <ins>{displayValue(item.field, item.after, session)}</ins>
        </div>
      ))}
    </section>
  );
}

export function GroupedCandidateDifferences({
  session,
  candidate,
}: {
  session: Session;
  candidate: EditCandidate;
}) {
  const registry = Object.values(session.componentRegistry ?? {});
  return (
    <details className="phase2-differences grouped-differences" open>
      <summary>查看具体差异 · {candidate.differences.length} 项</summary>
      {sessionDayItems(session).map((day) => {
        const differences = candidate.differences.filter((item) =>
          registry.some(
            (binding) =>
              binding.dayKey === day.key &&
              binding.entityId === item.activityId,
          ),
        );
        if (!differences.length) return null;
        return (
          <section key={day.key} aria-label={`${day.label}候选字段差异`}>
            <h4>
              {day.label} · {differences.length} 项
            </h4>
            <ul>
              {differences.map((item) => (
                <li
                  key={item.path}
                  data-derived={candidate.derivedChangedPaths.includes(
                    item.path,
                  )}
                >
                  <strong>
                    {item.title} · {fieldLabels[item.field]}
                  </strong>
                  <span>
                    {candidate.derivedChangedPaths.includes(item.path)
                      ? '地点或时间引起的联动变化'
                      : '授权字段直接修改'}
                  </span>
                  <del>{displayValue(item.field, item.before, session)}</del>
                  <ins>{displayValue(item.field, item.after, session)}</ins>
                  <code>{item.path}</code>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </details>
  );
}
