import { useEffect, useState } from 'react';
import { api } from '../../api';
import type { Session } from '../../types';
import type { FidelityReport } from './governanceTypes';

const percentage = (value: number | null) =>
  value === null ? '未验证' : `${(value * 100).toFixed(1)}%`;

export function FidelityPanel({
  session,
  busy,
  onReview,
}: {
  session: Session;
  busy: boolean;
  onReview: (
    targetVersion: number,
    requirementId: string,
    verdict: 'satisfied' | 'unsatisfied',
    note: string,
  ) => Promise<boolean>;
}) {
  const [report, setReport] = useState<FidelityReport | null>(null);
  const [error, setError] = useState('');
  const [selectedVersion, setSelectedVersion] = useState(session.version);
  const [note, setNote] = useState('');
  const [pending, setPending] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError('');
    api
      .fidelity(session.id, controller.signal)
      .then((next) => {
        if (!controller.signal.aborted) setReport(next);
      })
      .catch((value) => {
        if (!controller.signal.aborted)
          setError(
            value instanceof Error ? value.message : '读取验证记录失败。',
          );
      });
    return () => controller.abort();
  }, [session.id, session.version, reload]);
  const row =
    report?.rows.find((item) => item.version === selectedVersion) ??
    report?.rows.at(-1);
  const automatic =
    row?.requirements.filter((item) => item.status !== 'manual') ?? [];
  const semantic =
    row?.requirements.filter((item) => item.status === 'manual') ?? [];
  const reviewed = semantic.filter((item) => item.review);
  async function review(
    requirementId: string,
    verdict: 'satisfied' | 'unsatisfied',
  ) {
    if (!row) return;
    setPending(true);
    try {
      if (await onReview(row.version, requirementId, verdict, note))
        setNote('');
    } finally {
      setPending(false);
    }
  }
  function download() {
    if (!report) return;
    const blob = new Blob(
      [
        JSON.stringify(
          {
            format: 'travel-fidelity-report-v1',
            formalExperiment: false,
            ...report,
          },
          null,
          2,
        ),
      ],
      { type: 'application/json' },
    );
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `行程保真验证-v${report.currentVersion}.json`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <section
      className="fidelity-panel"
      data-testid="fidelity-panel"
      aria-label="多轮保真验证"
    >
      <div className="governance-actions">
        <button
          className="button secondary"
          onClick={() => setReload((value) => value + 1)}
        >
          刷新验证记录
        </button>
        <button
          className="button secondary"
          disabled={!report}
          onClick={download}
        >
          导出验证 JSON
        </button>
      </div>
      {error && (
        <p role="alert" className="phase2-error">
          {error}
        </p>
      )}
      {!report && !error && <p role="status">正在读取持久版本与验证证据…</p>}
      {report && (
        <>
          <div className="fidelity-metrics">
            <article>
              <span>状态保留率</span>
              <strong>{percentage(report.summary.stateRetentionRate)}</strong>
              <small>按受保护持久字段加权</small>
            </article>
            <article>
              <span>非目标修改</span>
              <strong>
                {report.summary.unauthorizedPathCount ?? '未验证'}
              </strong>
              <small>
                {report.summary.verifiedScopedEdits} 次受限编辑已重放
              </small>
            </article>
            <article>
              <span>结构功能检查</span>
              <strong>
                {report.summary.functionalChecksPassed}/
                {report.summary.functionalChecksTotal}
              </strong>
              <small>结构、时序、目录及稳定身份</small>
            </article>
            <article>
              <span>持久版本</span>
              <strong>{report.summary.versionCount}</strong>
              <small>当前 v{report.currentVersion}</small>
            </article>
          </div>
          <p className="governance-help">
            范围指标使用保存的授权和 Patch
            核对完整快照。需求管理、锁操作及历史授权不足的版本显示“未验证”。界面点击能力由浏览器回归测试另行核验。
          </p>
          <div className="table-scroll" tabIndex={0} aria-label="逐轮验证记录">
            <table className="fidelity-table">
              <thead>
                <tr>
                  <th>版本</th>
                  <th>操作</th>
                  <th>保留率</th>
                  <th>非目标修改</th>
                </tr>
              </thead>
              <tbody>
                {[...report.rows].reverse().map((item) => (
                  <tr
                    key={item.version}
                    className={
                      row?.version === item.version ? 'is-current' : ''
                    }
                  >
                    <td>
                      <button
                        className="button ghost"
                        aria-label={`查看 v${item.version} 验证`}
                        onClick={() => {
                          setSelectedVersion(item.version);
                          setNote('');
                        }}
                      >
                        v{item.version}
                      </button>
                    </td>
                    <td>{item.summary}</td>
                    <td>{percentage(item.stateRetentionRate)}</td>
                    <td>{item.unauthorizedPaths?.length ?? '未验证'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {row && (
            <section className="fidelity-detail" aria-label="当前版本验证详情">
              <h3>
                v{row.version} · {row.summary}
              </h3>
              <p>{row.scopeEvidence}</p>
              {row.protectedLeafCount !== null && (
                <p>
                  受保护字段 {row.protectedLeafCount} 个，保留{' '}
                  {row.preservedLeafCount} 个。
                </p>
              )}
              <ul className="fidelity-checks">
                {row.checks.map((check) => (
                  <li key={check.name} data-passed={check.passed}>
                    {check.passed ? '通过' : '失败'} · {check.name}
                  </li>
                ))}
                {row.lockedStatePreserved !== null && (
                  <li data-passed={row.lockedStatePreserved}>
                    锁定状态{row.lockedStatePreserved ? '保留' : '变化'}
                  </li>
                )}
              </ul>
              {!!row.unauthorizedPaths?.length && (
                <div role="alert" className="phase2-error">
                  <strong>发现非目标变化</strong>
                  {row.unauthorizedPaths.map((path) => (
                    <p key={path}>
                      <code>{path}</code>
                    </p>
                  ))}
                </div>
              )}
              <h4>本版本需求满足情况</h4>
              <p>
                自动校验通过{' '}
                {automatic.filter((item) => item.status === 'passed').length}/
                {automatic.length} · 人工已核验 {reviewed.length}/
                {semantic.length} · 待核验 {semantic.length - reviewed.length}
              </p>
              {!row.requirements.length && (
                <p className="empty-hint">该版本尚未确认持续需求。</p>
              )}
              {row.requirements.map((goal) => (
                <article key={goal.id} className="requirement-item">
                  <div className="governance-item-heading">
                    <strong>{goal.text}</strong>
                    <span
                      className={`governance-status ${goal.status === 'failed' || goal.review?.verdict === 'unsatisfied' ? 'is-failed' : ''}`}
                    >
                      {goal.status === 'manual'
                        ? goal.review
                          ? goal.review.verdict === 'satisfied'
                            ? '人工确认满足'
                            : '人工确认未满足'
                          : '待人工核验'
                        : goal.status === 'passed'
                          ? '自动通过'
                          : '尚未满足'}
                    </span>
                  </div>
                  <p>
                    {goal.scopeLabel} · 引入于 v{goal.introducedVersion} ·{' '}
                    {goal.review
                      ? `人工核验已记录于 v${goal.review.recordedVersion}`
                      : goal.detail}
                  </p>
                  {goal.review?.note && <p>核验说明 {goal.review.note}</p>}
                  {goal.status === 'manual' && (
                    <div className="governance-actions">
                      <button
                        className="button secondary"
                        disabled={busy || pending}
                        onClick={() => void review(goal.id, 'satisfied')}
                      >
                        确认满足
                      </button>
                      <button
                        className="button ghost"
                        disabled={busy || pending}
                        onClick={() => void review(goal.id, 'unsatisfied')}
                      >
                        标记未满足
                      </button>
                    </div>
                  )}
                </article>
              ))}
              {!!semantic.length && (
                <label className="governance-review-note">
                  人工核验说明（选填）
                  <textarea
                    aria-label="人工核验说明"
                    value={note}
                    maxLength={500}
                    onChange={(event) => setNote(event.target.value)}
                    disabled={busy || pending}
                    placeholder="填写对该版本需求的观察依据"
                  />
                </label>
              )}
              <details>
                <summary>
                  查看本版本完整持久差异 · {row.changedPaths.length} 项
                </summary>
                <ul className="version-paths">
                  {row.changedPaths.map((path) => (
                    <li key={path}>
                      <code>{path}</code>
                    </li>
                  ))}
                </ul>
              </details>
            </section>
          )}
        </>
      )}
    </section>
  );
}
