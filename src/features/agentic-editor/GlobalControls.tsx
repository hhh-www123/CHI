import { useEffect, useState } from 'react';
import { Check, ShieldCheck, Wallet } from 'lucide-react';
import type { Session } from '../../types';

type Confirmation = { baseVersion: number } & ({ kind: 'presentation'; showBudgetDetails: boolean } | { kind: 'constraint'; maxBudget: number | null });
export function GlobalControls({ session, busy, hasDrafts, onPresentation, onConstraint }: {
  session: Session; busy: boolean; hasDrafts: boolean;
  onPresentation: (show: boolean, baseVersion: number) => Promise<boolean>;
  onConstraint: (maxBudget: number | null, baseVersion: number) => Promise<boolean>;
}) {
  const showDetails = session.presentationSettings?.showBudgetDetails !== false;
  const cap = session.persistedConstraints?.hardConstraints.find(item => item.active && item.kind === 'max_budget');
  const capValue = typeof cap?.value === 'number' ? cap.value : null;
  const [budget, setBudget] = useState(String(capValue ?? session.tripState.budgetLimit));
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState('');
  useEffect(() => { setBudget(String(capValue ?? session.tripState.budgetLimit)); }, [session.id, capValue, session.tripState.budgetLimit]);
  useEffect(() => { setConfirmation(null); setConfirmed(false); }, [session.id, session.version]);
  const disabled = busy || pending || hasDrafts;
  function open(value: Confirmation) { setNotice(''); setConfirmed(false); setConfirmation(value); }
  function requestCap() {
    const value = Number(budget);
    if (!Number.isFinite(value) || value < 100 || value > 100000 || value < session.tripState.budgetLimit) { setNotice('硬上限应为100–100000元，且不能小于当前行程预算输入值。'); return; }
    open({ kind: 'constraint', baseVersion: session.version, maxBudget: value });
  }
  async function confirm() {
    if (!confirmation || !confirmed || disabled) return;
    if (confirmation.baseVersion !== session.version) { setNotice(`当前版本已变为 v${session.version}，请重新打开授权确认。`); setConfirmation(null); return; }
    setPending(true);
    try {
      const ok = confirmation.kind === 'presentation' ? await onPresentation(confirmation.showBudgetDetails, confirmation.baseVersion) : await onConstraint(confirmation.maxBudget, confirmation.baseVersion);
      if (ok) { setConfirmation(null); setConfirmed(false); setNotice('已保存本次明确授权的全局设置。'); }
      else setNotice('本次设置未保存，请根据提示重新确认。');
    } finally { setPending(false); }
  }
  return <section className="global-controls" data-testid="global-controls" aria-label="明确授权的全局展示与预算约束">
    <h3><Wallet size={15} />全局展示与预算约束</h3>
    <p>这些操作独立于当前局部选区；每次修改需单独确认并记录版本。</p>
    <button className="button secondary" type="button" data-testid="toggle-budget-details" disabled={disabled} onClick={() => open({ kind: 'presentation', baseVersion: session.version, showBudgetDetails: !showDetails })}>{showDetails ? '收起全局预算明细' : '展开全局预算明细'}</button>
    <div className="budget-constraint-control"><label htmlFor="max-budget-cap">预算输入硬上限（元）</label><input id="max-budget-cap" data-testid="max-budget-cap" type="number" min={100} max={100000} value={budget} disabled={disabled} onChange={event => setBudget(event.target.value)} /><span>{capValue == null ? '当前未设置硬上限' : `当前硬上限 ¥${capValue.toLocaleString('zh-CN')}`}</span><small>限制行程预算输入值；不是实际花费或实时费用承诺。时间顺序约束始终启用。</small><div><button className="button secondary" type="button" data-testid="save-budget-cap" disabled={disabled || Number(budget) === capValue} onClick={requestCap}>设置硬上限</button>{capValue != null && <button className="button ghost" type="button" data-testid="remove-budget-cap" disabled={disabled} onClick={() => open({ kind: 'constraint', baseVersion: session.version, maxBudget: null })}>移除硬上限</button>}</div></div>
    {notice && <p className="editor-status" role="status" data-testid="global-controls-notice">{notice}</p>}
    {confirmation && <section className="global-confirm" data-testid={confirmation.kind === 'presentation' ? 'global-confirm-dialog' : 'confirm-constraint-dialog'} role="alertdialog" aria-label={confirmation.kind === 'presentation' ? '确认全局预算展示' : '确认预算约束变更'}>
      <h4><ShieldCheck size={14} />{confirmation.kind === 'presentation' ? '确认全局展示修改' : '确认预算硬约束修改'}</h4>
      <p>{confirmation.kind === 'presentation' ? `本次仅${confirmation.showBudgetDetails ? '展开' : '收起'}全局预算明细，不修改预算值、景点、时点或组件锁。` : confirmation.maxBudget == null ? '本次明确移除预算输入硬上限，当前预算值、锁和时间顺序约束保持。' : `本次将预算输入硬上限设置为 ¥${confirmation.maxBudget.toLocaleString('zh-CN')}；后续普通编辑不能超过此上限或自动解除它。`}</p>
      <p>基于 v{confirmation.baseVersion}，确认后创建 v{confirmation.baseVersion + 1}。</p>
      <label><input type="checkbox" data-testid="confirm-global-authorization" checked={confirmed} disabled={busy || pending} onChange={event => setConfirmed(event.target.checked)} />我明确授权本次{confirmation.kind === 'presentation' ? '全局展示' : '硬约束'}变更。</label>
      <div className="dialog-actions"><button className="button secondary" type="button" disabled={busy || pending} onClick={() => setConfirmation(null)}>取消</button><button className="button primary" type="button" data-testid={confirmation.kind === 'presentation' ? 'confirm-global-presentation' : 'confirm-budget-constraint'} disabled={!confirmed || busy || pending} onClick={() => void confirm()}><Check size={14} />确认授权并保存</button></div>
    </section>}
  </section>;
}
