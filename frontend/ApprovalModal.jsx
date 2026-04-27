// ApprovalModal — handles SSE `approval_required` events.
//
// Shown as a modal overlay. On approve/reject:
//   - calls JarvisAPI.decideApproval()
//   - if approved, invokes onApproved(receipt) so caller can re-send the
//     chat message with `context.approval_receipt` populated
//   - if rejected, invokes onRejected()

const ApprovalModal = ({ approval, theme, onApproved, onRejected, onDismiss }) => {
  const [submitting, setSubmitting] = React.useState(false);
  const [error, setError] = React.useState('');
  const [comment, setComment] = React.useState('');

  if (!approval) return null;

  const t = theme || {};
  const surface = t.surface || '#0d1628';
  const accent  = t.accent  || '#3b82f6';
  const border  = t.border  || 'rgba(99,140,210,0.25)';
  const text    = t.text    || '#e2e8f0';
  const subtext = t.subtext || '#94a3b8';

  const riskColor = {
    low:      '#10b981',
    medium:   '#f59e0b',
    high:     '#f97316',
    critical: '#ef4444',
  }[approval.risk_level] || '#f59e0b';

  const decide = async (decision) => {
    setSubmitting(true);
    setError('');
    try {
      const result = await window.JarvisAPI.decideApproval(approval.request_id, decision, comment);
      if (decision === 'approve') {
        onApproved && onApproved(result.receipt);
      } else {
        onRejected && onRejected();
      }
    } catch (e) {
      setError(e && e.message ? e.message : '审批接口调用失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 1000,
    }}>
      <div style={{
        width: 520, maxWidth: '90vw', maxHeight: '85vh', overflow: 'auto',
        background: surface, border: `1px solid ${border}`, borderRadius: 12,
        padding: 24, color: text, boxShadow: '0 20px 60px rgba(0,0,0,0.5)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
          <div style={{
            width: 10, height: 10, borderRadius: '50%', background: riskColor,
            boxShadow: `0 0 12px ${riskColor}`,
          }} />
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>
            高风险操作待审批 · Approval Required
          </h3>
        </div>

        <div style={{ fontSize: 13, lineHeight: 1.6, marginBottom: 14 }}>
          <div><span style={{ color: subtext }}>动作 / Action：</span><b>{approval.action || 'unknown'}</b></div>
          <div><span style={{ color: subtext }}>风险等级 / Risk：</span>
            <span style={{ color: riskColor, fontWeight: 600, textTransform: 'uppercase' }}>
              {approval.risk_level || 'high'}
            </span>
          </div>
          <div><span style={{ color: subtext }}>请求 ID：</span>
            <code style={{ fontSize: 11, color: subtext }}>{approval.request_id}</code>
          </div>
        </div>

        {approval.payload && (
          <details style={{ marginBottom: 14 }}>
            <summary style={{ cursor: 'pointer', fontSize: 12, color: subtext }}>查看上下文 / Context</summary>
            <pre style={{
              marginTop: 8, padding: 12, background: 'rgba(0,0,0,0.3)',
              border: `1px solid ${border}`, borderRadius: 8, fontSize: 11,
              color: subtext, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
              maxHeight: 200, overflow: 'auto',
            }}>{JSON.stringify(approval.payload, null, 2)}</pre>
          </details>
        )}

        <div style={{ marginBottom: 14 }}>
          <label style={{ fontSize: 12, color: subtext, display: 'block', marginBottom: 4 }}>
            备注（可选） / Comment
          </label>
          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="审批理由 / 注意事项"
            disabled={submitting}
            style={{
              width: '100%', minHeight: 60, resize: 'vertical',
              padding: 8, fontSize: 13,
              background: 'rgba(0,0,0,0.25)',
              border: `1px solid ${border}`, borderRadius: 6,
              color: text,
            }}
          />
        </div>

        {error && (
          <div style={{
            padding: 10, marginBottom: 12,
            background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)',
            borderRadius: 6, color: '#fca5a5', fontSize: 12,
          }}>
            {error}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onDismiss} disabled={submitting} style={{
            padding: '8px 14px', background: 'transparent',
            border: `1px solid ${border}`, borderRadius: 6,
            color: subtext, cursor: submitting ? 'wait' : 'pointer', fontSize: 13,
          }}>稍后 / Later</button>
          <button onClick={() => decide('reject')} disabled={submitting} style={{
            padding: '8px 14px', background: 'rgba(239,68,68,0.1)',
            border: '1px solid rgba(239,68,68,0.4)', borderRadius: 6,
            color: '#fca5a5', cursor: submitting ? 'wait' : 'pointer', fontSize: 13,
          }}>拒绝 / Reject</button>
          <button onClick={() => decide('approve')} disabled={submitting} style={{
            padding: '8px 18px', background: accent,
            border: 'none', borderRadius: 6,
            color: '#fff', fontWeight: 600, cursor: submitting ? 'wait' : 'pointer', fontSize: 13,
          }}>{submitting ? '提交中…' : '批准 / Approve'}</button>
        </div>
      </div>
    </div>
  );
};
