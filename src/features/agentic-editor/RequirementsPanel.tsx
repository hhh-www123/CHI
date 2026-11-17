import { useEffect, useState, type FormEvent } from 'react';
import type { Session } from '../../types';
import type {
  Requirement,
  RequirementChange,
  RequirementKind,
} from './governanceTypes';

const KIND_LABELS: Record<RequirementKind, string> = {
  preference: '自由文本偏好',
  keep_hotel: '保留当前酒店',
  keep_component: '保留组件数据',
  cost_ceiling: '费用估算上限',
};

export function RequirementsPanel({
  session,
  busy,
  onChange,
}: {
  session: Session;
  busy: boolean;
  onChange: (
    change: RequirementChange,
    baseVersion: number,
  ) => Promise<boolean>;
}) {
  const [text, setText] = useState('');
  const [kind, setKind] = useState<RequirementKind>('preference');
  const [componentId, setComponentId] = useState('');
  const [maxCost, setMaxCost] = useState(
    String(Math.ceil(session.derived.total)),
  );
  const [editingId, setEditingId] = useState<string | null>(null);
  const [retiringId, setRetiringId] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const entries = session.requirements ?? [];
  const active = entries.filter((item) => item.status === 'active');
  const inactive = entries.filter((item) => item.status !== 'active');
  const bindings = Object.values(session.componentRegistry ?? {});

  useEffect(() => {
    setConfirmed(false);
    setRetiringId(null);
  }, [session.version]);

  function edit(item: Requirement) {
    setEditingId(item.id);
    setText(item.text);
    setKind(item.kind);
    setComponentId(item.componentId ?? '');
    setMaxCost(String(item.maxCost ?? Math.ceil(session.derived.total)));
    setConfirmed(false);
    setError('');
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!confirmed || busy || pending) return;
    setPending(true);
    setError('');
    const change: RequirementChange = {
      action: editingId ? 'replace' : 'add',
      confirm: true,
      text: text.trim(),
      kind,
      requirementId: editingId ?? undefined,
      componentId:
        kind === 'keep_component' || kind === 'preference'
          ? componentId || null
          : null,
      maxCost: kind === 'cost_ceiling' ? Number(maxCost) : null,
      sourceInstruction: text.trim(),
    };
    try {
      if (await onChange(change, session.version)) {
        setText('');
        setEditingId(null);
        setConfirmed(false);
      }
    } catch (value) {
      setError(value instanceof Error ? value.message : '需求保存失败。');
    } finally {
      setPending(false);
    }
  }
  async function retire(item: Requirement) {
    setPending(true);
    try {
      await onChange(
        { action: 'retire', requirementId: item.id, confirm: true },
        session.version,
      );
    } finally {
      setPending(false);
    }
  }
  return (
    <section
      className="requirements-panel"
      data-testid="requirements-panel"
      aria-label="历史需求追踪"
    >
      <p>
        持续需求随行程版本保存。新增需求会进入之后的候选上下文，替换和停用会记录生效版本。
      </p>
      <div className="requirement-list" aria-label="当前有效需求">
        {!active.length && (
          <p className="empty-hint">
            还没有确认的持续需求。可以先添加“保留当前酒店”或旅行偏好。
          </p>
        )}
        {active.map((item) => (
          <article
            key={item.id}
            className="requirement-item"
            data-testid="active-requirement"
          >
            <div className="governance-item-heading">
              <strong>{item.text}</strong>
              <span className="governance-status">有效</span>
            </div>
            <p>
              {KIND_LABELS[item.kind]} · {item.scopeLabel}
              {item.maxCost !== undefined &&
                ` · 上限 ¥${item.maxCost.toLocaleString('zh-CN')}`}{' '}
              · 从 v{item.introducedVersion} 起
            </p>
            <details>
              <summary>查看来源和替换关系</summary>
              <p>{item.sourceInstruction}</p>
              {item.replaces && (
                <p>
                  替换了旧需求{' '}
                  {entries.find((previous) => previous.id === item.replaces)
                    ?.text ?? item.replaces}
                </p>
              )}
            </details>
            <div className="governance-actions">
              <button
                className="button secondary"
                disabled={busy || pending}
                onClick={() => edit(item)}
              >
                替换此需求
              </button>
              <button
                className="button ghost"
                disabled={busy || pending}
                onClick={() => setRetiringId(item.id)}
              >
                停用此需求
              </button>
            </div>
            {retiringId === item.id && (
              <div
                className="requirement-confirm"
                role="alertdialog"
                aria-label="确认停用需求"
              >
                <p>
                  确认从下一版本停用“{item.text}
                  ”？后续候选将使用更新后的有效需求清单。
                </p>
                <button
                  className="button secondary"
                  disabled={pending}
                  onClick={() => setRetiringId(null)}
                >
                  保留需求
                </button>
                <button
                  className="button primary"
                  disabled={busy || pending}
                  onClick={() => void retire(item)}
                >
                  确认停用
                </button>
              </div>
            )}
          </article>
        ))}
      </div>
      <form className="requirement-form" onSubmit={(event) => void save(event)}>
        <h3>{editingId ? '替换持续需求' : '确认新的持续需求'}</h3>
        <label htmlFor="requirement-text">需求内容</label>
        <textarea
          id="requirement-text"
          required
          maxLength={500}
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            setConfirmed(false);
          }}
          disabled={busy || pending}
          placeholder="例如：同行者怕累，每天安排一次休息"
        />
        <div className="governance-form-grid">
          <label>
            校验方式
            <select
              aria-label="需求校验方式"
              value={kind}
              disabled={busy || pending}
              onChange={(event) => {
                setKind(event.target.value as RequirementKind);
                setConfirmed(false);
              }}
            >
              {Object.entries(KIND_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {(kind === 'preference' || kind === 'keep_component') && (
            <label>
              适用范围
              <select
                aria-label="需求适用范围"
                value={componentId}
                required={kind === 'keep_component'}
                disabled={busy || pending}
                onChange={(event) => {
                  setComponentId(event.target.value);
                  setConfirmed(false);
                }}
              >
                <option value="">
                  {kind === 'preference' ? '全程' : '请选择组件'}
                </option>
                {bindings.map((binding) => (
                  <option value={binding.componentId} key={binding.componentId}>
                    {binding.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          {kind === 'cost_ceiling' && (
            <label>
              估算费用上限（元）
              <input
                aria-label="需求估算费用上限"
                type="number"
                min={100}
                max={1000000}
                step={1}
                required
                value={maxCost}
                disabled={busy || pending}
                onChange={(event) => {
                  setMaxCost(event.target.value);
                  setConfirmed(false);
                }}
              />
            </label>
          )}
        </div>
        <p className="governance-help">
          {kind === 'preference'
            ? '偏好会送入候选上下文，满足情况在保真验证中逐轮人工核验。'
            : kind === 'keep_component'
              ? '确认时保存所选组件数据，之后的普通修改会校验其保留情况。'
              : kind === 'keep_hotel'
                ? '确认后持续校验当前酒店选择。'
                : '使用现有目录价格和人数计算费用。未达成时允许分步降低费用，已达成后保护该上限。'}
        </p>
        <label className="governance-confirm">
          <input
            type="checkbox"
            checked={confirmed}
            disabled={busy || pending}
            onChange={(event) => setConfirmed(event.target.checked)}
          />
          我确认此需求、范围和校验方式，从下一版本生效。
        </label>
        {error && (
          <p role="alert" className="phase2-error">
            {error}
          </p>
        )}
        <div className="governance-actions">
          <button
            className="button primary"
            type="submit"
            disabled={busy || pending || !confirmed || !text.trim()}
          >
            {pending
              ? '正在保存…'
              : editingId
                ? '确认替换需求'
                : '保存持续需求'}
          </button>
          {editingId && (
            <button
              className="button ghost"
              type="button"
              disabled={pending}
              onClick={() => {
                setEditingId(null);
                setText('');
                setConfirmed(false);
              }}
            >
              取消替换
            </button>
          )}
        </div>
      </form>
      {!!inactive.length && (
        <details className="retired-requirements">
          <summary>已替换或停用的需求 · {inactive.length}</summary>
          {inactive.map((item) => (
            <article key={item.id}>
              <strong>{item.text}</strong>
              <p>
                {item.status === 'superseded' ? '已替换' : '已停用'} · v
                {item.introducedVersion} 至 v{item.endedVersion}
                {item.supersededBy &&
                  ` · 新需求 ${entries.find((next) => next.id === item.supersededBy)?.text ?? ''}`}
              </p>
            </article>
          ))}
        </details>
      )}
    </section>
  );
}
