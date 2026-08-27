import { useState } from 'react';
import { useStore } from '../store/StoreContext';
import { api } from '../api/client';
import { PaperPlaneRight, WarningCircle } from '@phosphor-icons/react';

/**
 * Asking a question about the shop's own figures.
 *
 * The period is picked here rather than worked out from the wording of
 * the question. That is the point of the control: the answer is about a
 * window the person can see on the screen while they read it, instead of
 * one they have to trust was understood correctly. "เทียบสงกรานต์ปีที่แล้ว"
 * is answered by moving these two dates, which is also how they find out
 * whether there is any data back there at all.
 *
 * Every figure in the answer was worked out by the backend before the
 * model was asked (core/assistant.py), and checked against those figures
 * afterwards. Anything that failed the check is shown here rather than
 * quietly dropped - the reader is the only one who can decide whether to
 * go and look.
 */

const SUGGESTIONS = [
  'เดือนนี้เป็นยังไงบ้าง',
  'เทียบกับช่วงก่อนหน้าดีขึ้นหรือแย่ลง',
  'เมนูไหนขายดีที่สุด',
  'กำไรหายไปไหน',
];

function thisMonth() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return {
    from: `${now.getFullYear()}-${pad(now.getMonth() + 1)}-01`,
    to: `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`,
  };
}

export default function Assistant() {
  const { storeId } = useStore();
  const [range, setRange] = useState(thisMonth);
  const [question, setQuestion] = useState('');
  const [result, setResult] = useState(null);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState('');

  async function ask(text) {
    const q = (text ?? question).trim();
    if (!q || asking) return;
    setAsking(true);
    setError('');
    setResult(null);
    try {
      const r = await api.askAssistant(storeId, q, range.from, range.to);
      if (r.ok) setResult({ ...r, question: q });
      else setError(r.error || 'ผู้ช่วยตอบไม่ได้ตอนนี้');
    } catch (e) {
      setError(e.message);
    } finally {
      setAsking(false);
    }
  }

  if (!storeId) return <p>เลือกสาขาในหน้าตั้งค่าก่อน</p>;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>ถามข้อมูลร้าน</h1>
          <p className="page-subtitle">
            ตอบจากข้อมูลในระบบเท่านั้น ไม่ได้เดา — ถ้าไม่มีข้อมูลจะบอกว่าไม่มี
          </p>
        </div>
      </div>

      <div style={{
        display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap',
        marginBottom: 14, fontSize: 13,
      }}>
        <span style={{ color: 'var(--text-muted)' }}>ช่วงที่ถาม</span>
        <input type="date" value={range.from} max={range.to}
          onChange={(e) => setRange({ ...range, from: e.target.value })}
          style={dateStyle} />
        <span style={{ color: 'var(--text-muted)' }}>ถึง</span>
        <input type="date" value={range.to} min={range.from}
          onChange={(e) => setRange({ ...range, to: e.target.value })}
          style={dateStyle} />
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') ask(); }}
          placeholder="พิมพ์คำถาม เช่น เดือนนี้กำไรเท่าไหร่"
          maxLength={400}
          style={{
            flex: 1, minWidth: 0, fontSize: 14, padding: '11px 13px',
            borderRadius: 10, background: 'var(--surface-1)',
            border: '1px solid var(--border)',
          }}
        />
        <button className="button-primary" onClick={() => ask()}
          disabled={asking || !question.trim()}>
          <PaperPlaneRight size={18} />
          {asking ? 'กำลังคิด...' : 'ถาม'}
        </button>
      </div>

      {!result && !asking && (
        <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginBottom: 18 }}>
          {SUGGESTIONS.map((s) => (
            <button key={s} onClick={() => { setQuestion(s); ask(s); }} style={{
              fontSize: 12.5, padding: '7px 11px', borderRadius: 999,
              background: 'var(--surface-2)', border: '1px solid var(--border)',
              color: 'var(--text-secondary)', cursor: 'pointer',
            }}>{s}</button>
          ))}
        </div>
      )}

      {error && (
        <p style={{ fontSize: 13, color: 'var(--text-danger)' }}>{error}</p>
      )}

      {result && (
        <div style={{
          background: 'var(--surface-2)', border: '1px solid var(--border)',
          borderRadius: 10, padding: '14px 16px',
        }}>
          <p style={{
            fontSize: 12.5, color: 'var(--text-muted)', margin: '0 0 8px',
          }}>
            {result.question} · {result.from} ถึง {result.to}
          </p>
          {result.answer.split('\n').filter(Boolean).map((line, i) => (
            <p key={i} style={{ margin: i === 0 ? 0 : '8px 0 0', fontSize: 14, lineHeight: 1.6 }}>
              {line}
            </p>
          ))}

          {/* Not hidden. A figure the data cannot account for is still
              worth reading - it is just not worth acting on without
              checking, and only the person reading can be told that. */}
          {result.unverified_numbers?.length > 0 && (
            <p style={{
              display: 'flex', gap: 7, alignItems: 'flex-start',
              marginTop: 12, paddingTop: 11, borderTop: '1px solid var(--border)',
              fontSize: 12.5, color: 'var(--text-warning)',
            }}>
              <WarningCircle size={16} style={{ flexShrink: 0, marginTop: 1 }} />
              <span>
                ตัวเลข {result.unverified_numbers.map((n) => n.toLocaleString('en-US')).join(', ')}{' '}
                หาไม่เจอในข้อมูลของร้าน — เช็กจากหน้ารายรับรายจ่ายอีกทีก่อนใช้ตัดสินใจ
              </span>
            </p>
          )}

          {result.caveats?.length > 0 && (
            <div style={{
              marginTop: 12, paddingTop: 11, borderTop: '1px solid var(--border)',
            }}>
              {result.caveats.map((c) => (
                <p key={c.kind} style={{
                  margin: '0 0 5px', fontSize: 12.5, color: 'var(--text-secondary)',
                }}>
                  · {c.message}
                  {c.items?.length > 0 && ` (${c.items.slice(0, 5).join(', ')})`}
                </p>
              ))}
            </div>
          )}

          <p style={{
            margin: '12px 0 0', fontSize: 11.5, color: 'var(--text-muted)',
          }}>
            วันนี้ถามไปแล้ว {result.asks_today}/{result.daily_limit} คำถาม
          </p>
        </div>
      )}
    </div>
  );
}

const dateStyle = {
  fontSize: 13, padding: '7px 9px', borderRadius: 8,
  background: 'var(--surface-1)', border: '1px solid var(--border)',
};
