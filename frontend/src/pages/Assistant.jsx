import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useStore } from '../store/StoreContext';
import { api } from '../api/client';
import {
  ArrowRight, BookmarkSimple, CheckCircle, LockKey, PaperPlaneRight,
  Play, WarningCircle, X,
} from '@phosphor-icons/react';
import SetupGate from '../components/SetupGate';

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

const TODAY = thisMonth().to;

export default function Assistant() {
  const { storeId, stores } = useStore();
  const navigate = useNavigate();
  const [range, setRange] = useState(thisMonth);
  const [question, setQuestion] = useState('');
  const [result, setResult] = useState(null);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState('');
  const [previousQuestions, setPreviousQuestions] = useState([]);
  const [insights, setInsights] = useState([]);
  const [analysis, setAnalysis] = useState(null);
  const [insightsLoading, setInsightsLoading] = useState(false);
  const [insightsError, setInsightsError] = useState('');
  const [tracking, setTracking] = useState([]);
  const [trackingError, setTrackingError] = useState('');
  const [trackingBusy, setTrackingBusy] = useState('');
  const activeStore = useMemo(
    () => stores.find((store) => store.id === storeId), [stores, storeId]);
  const comparisonPeriod = useMemo(
    () => comparisonPeriods(range.from, range.to), [range.from, range.to]);

  useEffect(() => {
    if (!storeId || !range.from || !range.to) return undefined;
    let active = true;
    setPreviousQuestions([]);
    setResult(null);
    setInsightsLoading(true);
    setInsightsError('');
    api.getAssistantInsights(storeId, range.from, range.to)
      .then((response) => {
        if (active) {
          setInsights(response.recommendations || []);
          setAnalysis(response.analysis || null);
        }
      })
      .catch((e) => {
        if (active) {
          setInsights([]);
          setAnalysis(null);
          setInsightsError(e.message);
        }
      })
      .finally(() => { if (active) setInsightsLoading(false); });
    return () => { active = false; };
  }, [storeId, range.from, range.to]);

  useEffect(() => {
    if (!storeId) return undefined;
    let active = true;
    api.getAssistantTracking(storeId)
      .then((rows) => { if (active) setTracking(rows || []); })
      .catch((e) => { if (active) setTrackingError(e.message); });
    return () => { active = false; };
  }, [storeId]);

  async function saveTracking(recommendationId) {
    if (trackingBusy) return;
    setTrackingBusy(`save:${recommendationId}`);
    setTrackingError('');
    try {
      const saved = await api.createAssistantTracking(
        storeId, recommendationId, range.from, range.to);
      setTracking((rows) => [saved, ...rows]);
    } catch (e) {
      setTrackingError(e.message);
    } finally {
      setTrackingBusy('');
    }
  }

  async function setTrackingStatus(row, status) {
    setTrackingBusy(row.id);
    setTrackingError('');
    try {
      const updated = await api.updateAssistantTracking(storeId, row.id, status);
      setTracking((rows) => rows.map((item) => item.id === row.id ? updated : item));
    } catch (e) {
      setTrackingError(e.message);
    } finally {
      setTrackingBusy('');
    }
  }

  async function evaluateTracking(row) {
    const window = nextEvaluationWindow(row.baseline?.period);
    if (!window || trackingBusy) return;
    setTrackingBusy(row.id);
    setTrackingError('');
    try {
      const updated = await api.evaluateAssistantTracking(
        storeId, row.id, window.from, window.to);
      setTracking((rows) => rows.map((item) => item.id === row.id ? updated : item));
    } catch (e) {
      setTrackingError(e.message);
    } finally {
      setTrackingBusy('');
    }
  }

  async function ask(text) {
    const q = (text ?? question).trim();
    if (!q || asking) return;
    setAsking(true);
    setError('');
    setResult(null);
    try {
      const r = await api.askAssistant(storeId, q, range.from, range.to, previousQuestions);
      if (r.ok) {
        setResult({ ...r, question: q });
        setPreviousQuestions((items) => [...items, q].slice(-4));
        // The answer already carries the recommendations for this period -
        // the request worked them out to answer the question. Taking them
        // from here keeps the panel and the answer talking about the same
        // figures, and saves asking /insights for them a second time.
        if (r.recommendations) setInsights(r.recommendations);
      }
      else setError(r.error || 'ผู้ช่วยตอบไม่ได้ตอนนี้');
    } catch (e) {
      setError(e.message);
    } finally {
      setAsking(false);
    }
  }

  if (!storeId) return <SetupGate what="ถามข้อมูลร้านได้" />;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>ผู้ช่วยวิเคราะห์ร้าน</h1>
          <p className="page-subtitle">
            {activeStore?.name ? `${activeStore.name} · ` : ''}
            ตอบจากข้อมูลในระบบเท่านั้น ถ้าไม่มีข้อมูลจะบอกว่าไม่มี
          </p>
        </div>
      </div>

      <div style={privacyStyle}>
        <LockKey size={18} style={{ flexShrink: 0 }} />
        <span>
          ผู้ช่วยอ่านข้อมูลได้อย่างเดียวและแก้ไขข้อมูลร้านไม่ได้ · เมื่อถาม AI ระบบจะส่งเฉพาะ
          ข้อมูลสรุปยอดขาย ต้นทุน กำไร และชื่อเมนูไปยัง Google Gemini โดยไม่ส่งข้อมูลลูกค้า
          พนักงาน หรือรายละเอียดบิลรายใบ
        </span>
      </div>

      <div style={{
        display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap',
        marginBottom: 14, fontSize: 13,
      }}>
        <span style={{ color: 'var(--text-muted)' }}>ช่วงที่ถาม</span>
        <input type="date" value={range.from} max={range.to < TODAY ? range.to : TODAY}
          onChange={(e) => setRange({ ...range, from: e.target.value })}
          style={dateStyle} />
        <span style={{ color: 'var(--text-muted)' }}>ถึง</span>
        <input type="date" value={range.to} min={range.from} max={TODAY}
          onChange={(e) => setRange({ ...range, to: e.target.value })}
          style={dateStyle} />
      </div>

      <section aria-labelledby="assistant-priorities" style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 9 }}>
          <h2 id="assistant-priorities" style={{ margin: 0, fontSize: 17 }}>
            ควรเริ่มตรงไหนก่อน
          </h2>
          <span style={{ color: 'var(--text-muted)', fontSize: 12.5 }}>
            จากช่วงที่เลือก · ไม่แก้ข้อมูลให้อัตโนมัติ
          </span>
        </div>
        <div aria-live="polite">
          {insightsLoading && <p style={mutedStyle}>กำลังวิเคราะห์ข้อมูล...</p>}
          {insightsError && <p role="alert" style={errorStyle}>{insightsError}</p>}
          {!insightsLoading && !insightsError && insights.length === 0 && (
            <p style={mutedStyle}>ยังไม่มีข้อมูลพอสำหรับจัดลำดับคำแนะนำในช่วงนี้</p>
          )}
          {insights.length > 0 && (
            <div style={insightGridStyle}>
              {insights.map((item, index) => (
                <article key={item.id} style={insightCardStyle}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <span style={rankStyle}>อันดับ {index + 1} · {item.category}</span>
                    <span style={confidenceStyle}>มั่นใจ{item.confidence}</span>
                  </div>
                  <h3 style={{ margin: '9px 0 5px', fontSize: 15 }}>{item.title}</h3>
                  <p style={{ margin: 0, fontSize: 13, lineHeight: 1.55 }}>{item.reason}</p>
                  <p style={{ margin: '8px 0 0', fontSize: 12.5, color: 'var(--text-secondary)' }}>
                    หลักฐาน: {item.evidence}
                  </p>
                  <p style={{ margin: '5px 0 0', fontSize: 11.5, color: 'var(--text-muted)' }}>
                    ข้อจำกัด: {item.limitation}
                  </p>
                  <button type="button" onClick={() => navigate(item.action.path)} style={actionStyle}>
                    {item.action.label}<ArrowRight size={14} />
                  </button>
                  <button type="button" onClick={() => saveTracking(item.id)}
                    disabled={trackingBusy !== '' || tracking.some((row) =>
                      row.recommendation?.id === item.id && row.status !== 'cancelled')}
                    style={{ ...actionStyle, marginLeft: 14 }}>
                    <BookmarkSimple size={14} />
                    {trackingBusy === `save:${item.id}`
                      ? 'กำลังเก็บ...'
                      : tracking.some((row) => row.recommendation?.id === item.id
                        && row.status !== 'cancelled')
                        ? 'อยู่ในแผนแล้ว' : 'เก็บเป็นแผนติดตาม'}
                  </button>
                </article>
              ))}
            </div>
          )}
        </div>
      </section>

      {analysis && (
        <section aria-labelledby="deep-analysis" style={{ marginBottom: 22 }}>
          <div style={{ marginBottom: 9 }}>
            <h2 id="deep-analysis" style={{ margin: 0, fontSize: 17 }}>
              ผลประกอบการ {comparisonPeriod.current}
            </h2>
            <p style={{ margin: '2px 0 0', fontSize: 12, color: 'var(--text-muted)' }}>
              เปรียบเทียบกับ {comparisonPeriod.previous} ซึ่งมีจำนวนวันเท่ากัน
            </p>
          </div>

          {analysis.period_changes && (
            <div style={changeGridStyle}>
              <ChangeCard label="ยอดขาย"
                value={analysis.period_changes.sales_baht} />
              <ChangeCard label="ต้นทุนตามสูตร"
                value={analysis.period_changes.ingredient_cost_baht} favorable="down" />
              <ChangeCard label="ยอดซื้อวัตถุดิบ"
                value={analysis.period_changes.purchases_baht} favorable="down" />
              <ChangeCard label="กำไรสุทธิ"
                value={analysis.period_changes.net_profit_baht} />
            </div>
          )}

          {analysis.signals?.length > 0 && (
            <div style={{ display: 'grid', gap: 7, margin: '10px 0' }}>
              {analysis.signals.map((signal) => (
                <div key={signal.kind} style={signalStyle}>
                  <WarningCircle size={17} style={{ flexShrink: 0, marginTop: 1 }} />
                  <span><strong>{signal.title}</strong><br />{signal.detail}</span>
                </div>
              ))}
            </div>
          )}

          {analysis.menus?.lowest_margin?.length > 0 && (
            <div style={tableCardStyle}>
              <div style={{ padding: '12px 14px 8px' }}>
                <h3 style={{ margin: 0, fontSize: 15 }}>กำไรขั้นต้นรายเมนูที่ควรตรวจ</h3>
                <p style={{ margin: '3px 0 0', fontSize: 11.5, color: 'var(--text-muted)' }}>
                  เรียงจากอัตรากำไรต่ำสุด · {analysis.method}
                </p>
              </div>
              <div style={{ overflowX: 'auto' }}>
                <table style={analysisTableStyle}>
                  <thead><tr>
                    <th>เมนู</th><th>ขาย</th><th>ยอดขาย</th><th>ต้นทุน/จาน</th>
                    <th>กำไรขั้นต้น</th><th>อัตรากำไร</th><th>ยอดขายเทียบช่วงก่อน</th>
                  </tr></thead>
                  <tbody>
                    {analysis.menus.lowest_margin.map((menu) => (
                      <tr key={menu.name}>
                        <td style={{ fontWeight: 600 }}>{menu.name}</td>
                        <td>{formatNumber(menu.qty)}</td>
                        <td>{formatBaht(menu.revenue)}</td>
                        <td>{formatBaht(menu.unit_cost)}</td>
                        <td>{formatBaht(menu.gross_profit)}</td>
                        <td>{formatNumber(menu.gross_margin_pct)}%</td>
                        <td style={changeColor(menu.revenue_change_baht)}>
                          {menu.revenue_change_baht == null
                            ? 'ไม่มีข้อมูลเทียบ'
                            : `${formatChange(menu.revenue_change_baht)} บาท`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {analysis.menus.uncosted?.length > 0 && (
                <p style={{ margin: 0, padding: '9px 14px 12px', fontSize: 12,
                  color: 'var(--text-warning)' }}>
                  ยังไม่รวมกำไรของ: {analysis.menus.uncosted.slice(0, 6).join(', ')}
                </p>
              )}
            </div>
          )}
        </section>
      )}

      <section aria-labelledby="tracking-title" style={{ marginBottom: 22 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 9 }}>
          <h2 id="tracking-title" style={{ margin: 0, fontSize: 17 }}>แผนที่กำลังติดตาม</h2>
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            คุณเป็นผู้เริ่ม เปลี่ยนสถานะ และวัดผลเอง
          </span>
        </div>
        {trackingError && <p role="alert" style={errorStyle}>{trackingError}</p>}
        {tracking.length === 0 ? (
          <p style={mutedStyle}>ยังไม่มีแผน กด “เก็บเป็นแผนติดตาม” จากคำแนะนำด้านบนได้เลย</p>
        ) : (
          <div style={{ display: 'grid', gap: 9 }}>
            {tracking.map((row) => (
              <TrackingCard key={row.id} row={row} busy={trackingBusy === row.id}
                onStatus={setTrackingStatus} onEvaluate={evaluateTracking} />
            ))}
          </div>
        )}
      </section>

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
      {previousQuestions.length > 0 && (
        <p style={{ margin: '-5px 0 10px', fontSize: 11.5, color: 'var(--text-muted)' }}>
          ผู้ช่วยจำบริบท {previousQuestions.length} คำถามล่าสุดในหน้านี้ · ไม่บันทึกเป็นข้อมูลร้าน
        </p>
      )}

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
        <p role="alert" style={errorStyle}>{error}</p>
      )}

      {result && (
        <div aria-live="polite" style={{
          background: 'var(--surface-2)', border: '1px solid var(--border)',
          borderRadius: 10, padding: '14px 16px',
        }}>
          <p style={{
            fontSize: 12.5, color: 'var(--text-muted)', margin: '0 0 8px',
          }}>
            {result.question} · {result.from} ถึง {result.to}
          </p>
          {result.decision_support && (
            <DecisionSupportCard data={result.decision_support} navigate={navigate} />
          )}
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

          {!result.decision_support && result.caveats?.length > 0 && (
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

const privacyStyle = {
  display: 'flex', gap: 9, alignItems: 'flex-start', margin: '-3px 0 16px',
  padding: '10px 12px', borderRadius: 10, fontSize: 12.5, lineHeight: 1.5,
  color: 'var(--text-secondary)', background: 'var(--surface-2)',
  border: '1px solid var(--border)',
};
const insightGridStyle = {
  display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: 10,
};
const insightCardStyle = {
  border: '1px solid var(--border)', borderRadius: 12, padding: '13px 14px',
  background: 'var(--surface-1)',
};
const rankStyle = { fontSize: 11.5, color: 'var(--accent)', fontWeight: 700 };
const confidenceStyle = {
  fontSize: 10.5, color: 'var(--text-secondary)', background: 'var(--surface-2)',
  padding: '2px 7px', borderRadius: 999,
};
const actionStyle = {
  display: 'inline-flex', alignItems: 'center', gap: 5, marginTop: 10, padding: 0,
  border: 0, background: 'transparent', color: 'var(--accent)', fontSize: 12.5,
  fontWeight: 600, cursor: 'pointer',
};
const mutedStyle = { margin: 0, fontSize: 13, color: 'var(--text-muted)' };
const errorStyle = { fontSize: 13, color: 'var(--text-danger)' };
const changeGridStyle = {
  display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8,
};
const changeCardStyle = {
  border: '1px solid var(--border)', borderRadius: 10, padding: '10px 12px',
  background: 'var(--surface-1)',
};
const signalStyle = {
  display: 'flex', gap: 8, alignItems: 'flex-start', padding: '9px 11px',
  borderRadius: 9, fontSize: 12.5, lineHeight: 1.45,
  color: 'var(--text-secondary)', background: 'var(--surface-2)',
};
const tableCardStyle = {
  border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden',
  background: 'var(--surface-1)', marginTop: 10,
};
const analysisTableStyle = {
  width: '100%', minWidth: 720, borderCollapse: 'collapse', fontSize: 12.5,
};

function ChangeCard({ label, value, favorable = 'up' }) {
  return (
    <div style={changeCardStyle}>
      <p style={{ margin: 0, fontSize: 11.5, color: 'var(--text-muted)' }}>{label}</p>
      <p style={{ margin: '4px 0 0', fontSize: 19, fontWeight: 700,
        color: 'var(--text-primary)' }}>
        {formatBaht(value.current)}
      </p>
      <p style={{ margin: '3px 0 0', fontSize: 10.5, color: 'var(--text-muted)' }}>
        ก่อนหน้า {formatBaht(value.previous)}
      </p>
      <p style={{ margin: '2px 0 0', fontSize: 11.5, fontWeight: 600,
        ...changeColor(value.change, favorable) }}>
        {changeDescription(value.change, value.change_pct)}
      </p>
    </div>
  );
}

function formatBaht(value) {
  return `${Number(value || 0).toLocaleString('th-TH', { maximumFractionDigits: 2 })} บาท`;
}
function formatNumber(value) {
  return Number(value || 0).toLocaleString('th-TH', { maximumFractionDigits: 1 });
}
function formatChange(value) {
  if (value == null) return 'ไม่มีข้อมูลเทียบ';
  const number = Number(value);
  return `${number > 0 ? '+' : ''}${number.toLocaleString('th-TH', { maximumFractionDigits: 2 })}`;
}
function changeColor(value, favorable = 'up') {
  if (value == null || Number(value) === 0) return { color: 'var(--text-muted)' };
  const good = favorable === 'up' ? Number(value) > 0 : Number(value) < 0;
  return { color: good ? 'var(--success)' : 'var(--text-danger)' };
}

function changeDescription(change, percentage) {
  const value = Number(change || 0);
  if (value === 0) return 'ไม่เปลี่ยนแปลงจากช่วงก่อน';
  const direction = value > 0 ? 'เพิ่มขึ้น' : 'ลดลง';
  const amount = Math.abs(value).toLocaleString('th-TH', { maximumFractionDigits: 2 });
  const pct = percentage == null ? ''
    : ` (${Math.abs(Number(percentage)).toLocaleString('th-TH', { maximumFractionDigits: 1 })}%)`;
  return `${direction} ${amount} บาท${pct}`;
}

function comparisonPeriods(from, to) {
  const start = utcDay(from);
  const end = utcDay(to);
  if (!start || !end || end < start) return { current: '', previous: '' };
  const day = 86400000;
  const days = Math.round((end - start) / day) + 1;
  const previousEnd = new Date(start.getTime() - day);
  const previousStart = new Date(previousEnd.getTime() - ((days - 1) * day));
  return {
    current: formatThaiRange(start, end),
    previous: formatThaiRange(previousStart, previousEnd),
  };
}

const THAI_MONTHS = ['ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.',
  'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.'];

function utcDay(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
  return match ? new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1,
    Number(match[3]))) : null;
}

function formatThaiRange(start, end) {
  const startDay = start.getUTCDate();
  const endDay = end.getUTCDate();
  const startMonth = THAI_MONTHS[start.getUTCMonth()];
  const endMonth = THAI_MONTHS[end.getUTCMonth()];
  const startYear = start.getUTCFullYear() + 543;
  const endYear = end.getUTCFullYear() + 543;
  if (startYear === endYear && start.getUTCMonth() === end.getUTCMonth()) {
    return `${startDay}–${endDay} ${endMonth} ${endYear}`;
  }
  if (startYear === endYear) {
    return `${startDay} ${startMonth}–${endDay} ${endMonth} ${endYear}`;
  }
  return `${startDay} ${startMonth} ${startYear}–${endDay} ${endMonth} ${endYear}`;
}

const STATUS_LABEL = {
  planned: 'วางแผนไว้', in_progress: 'กำลังทดลอง',
  completed: 'วัดผลแล้ว', cancelled: 'ยกเลิก',
};

function TrackingCard({ row, busy, onStatus, onEvaluate }) {
  const evaluationWindow = nextEvaluationWindow(row.baseline?.period);
  const result = row.evaluation?.metrics;
  return (
    <article style={trackingCardStyle}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10,
        alignItems: 'flex-start' }}>
        <div>
          <span style={rankStyle}>{STATUS_LABEL[row.status] || row.status}</span>
          <h3 style={{ margin: '4px 0', fontSize: 14.5 }}>{row.recommendation?.title}</h3>
          <p style={{ margin: 0, fontSize: 11.5, color: 'var(--text-muted)' }}>
            ข้อมูลก่อนเริ่ม {row.baseline?.period?.from} ถึง {row.baseline?.period?.to}
          </p>
        </div>
      </div>

      {result && (
        <div style={trackingResultStyle}>
          <span>ยอดขาย {formatChange(result.sales_baht.change)} บาท</span>
          <span>ยอดซื้อ {formatChange(result.purchases_baht.change)} บาท</span>
          <span>กำไรสุทธิ {formatChange(result.net_profit_baht.change)} บาท</span>
          <small style={{ gridColumn: '1 / -1', color: 'var(--text-muted)' }}>
            {row.evaluation.interpretation}
          </small>
        </div>
      )}

      {row.status !== 'completed' && row.status !== 'cancelled' && (
        <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginTop: 10 }}>
          {row.status === 'planned' && (
            <button className="button-secondary" disabled={busy}
              onClick={() => onStatus(row, 'in_progress')}>
              <Play size={15} /> เริ่มทดลอง
            </button>
          )}
          {row.status === 'in_progress' && evaluationWindow && (
            <button className="button-primary" disabled={busy}
              onClick={() => onEvaluate(row)}>
              <CheckCircle size={15} /> วัดผลช่วงถัดไป
            </button>
          )}
          {row.status === 'in_progress' && !evaluationWindow && (
            <span style={{ fontSize: 11.5, color: 'var(--text-muted)', alignSelf: 'center' }}>
              รอให้ครบช่วงเวลาเท่ากับข้อมูลก่อนเริ่ม จึงจะวัดผลได้
            </span>
          )}
          <button className="button-secondary" disabled={busy}
            onClick={() => onStatus(row, 'cancelled')}>
            <X size={15} /> ยกเลิกแผน
          </button>
        </div>
      )}
    </article>
  );
}

const trackingCardStyle = {
  padding: '12px 14px', borderRadius: 11, border: '1px solid var(--border)',
  background: 'var(--surface-1)',
};
const trackingResultStyle = {
  display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
  gap: 7, marginTop: 10, padding: '9px 10px', borderRadius: 8,
  background: 'var(--surface-2)', fontSize: 12.5,
};

function nextEvaluationWindow(period) {
  if (!period?.to || !period?.days) return null;
  const start = addDays(period.to, 1);
  const end = addDays(start, Number(period.days) - 1);
  if (end >= TODAY) return null;
  return { from: start, to: end };
}

function addDays(day, amount) {
  const date = new Date(`${day}T12:00:00`);
  date.setDate(date.getDate() + amount);
  const pad = (value) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function DecisionSupportCard({ data, navigate }) {
  const conclusion = data.conclusion || {};
  const action = data.next_action;
  return (
    <div style={decisionCardStyle}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8,
        alignItems: 'flex-start' }}>
        <div>
          <span style={rankStyle}>{data.intent_label}</span>
          <h3 style={{ margin: '4px 0 0', fontSize: 16 }}>{conclusion.label}</h3>
        </div>
        <span style={confidenceStyle}>มั่นใจ{data.confidence}</span>
      </div>

      {data.evidence?.length > 0 && (
        <div style={decisionEvidenceStyle}>
          {data.evidence.slice(0, 6).map((row, index) => (
            <div key={`${row.label}-${index}`}>
              <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>{row.label}</span>
              {row.value != null && (
                <strong style={{ display: 'block', marginTop: 1, fontSize: 13 }}>
                  {formatEvidence(row.value, row.unit)}
                </strong>
              )}
              {row.detail && <small style={{ color: 'var(--text-secondary)' }}>{row.detail}</small>}
            </div>
          ))}
        </div>
      )}

      {data.missing_data?.length > 0 && (
        <p style={{ margin: '9px 0 0', fontSize: 12.5, color: 'var(--text-secondary)' }}>
          <strong>ข้อมูลที่ยังขาด:</strong> {data.missing_data.join(', ')}
        </p>
      )}
      {data.limitation && (
        <p style={{ margin: '5px 0 0', fontSize: 11.5, color: 'var(--text-muted)' }}>
          ข้อจำกัด: {data.limitation}
        </p>
      )}
      {action?.path && (
        <button type="button" style={actionStyle} onClick={() => navigate(action.path)}>
          {action.label}<ArrowRight size={14} />
        </button>
      )}
      {action?.type === 'track' && !action.path && (
        <p style={{ margin: '8px 0 0', fontSize: 12, color: 'var(--accent)' }}>
          ขั้นตอนถัดไป: {action.label}
        </p>
      )}
    </div>
  );
}

const decisionCardStyle = {
  marginBottom: 12, padding: '12px 13px', borderRadius: 10,
  border: '1px solid color-mix(in srgb, var(--accent) 28%, var(--border))',
  background: 'var(--surface-1)',
};
const decisionEvidenceStyle = {
  display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(125px, 1fr))',
  gap: 8, marginTop: 10, paddingTop: 9, borderTop: '1px solid var(--border)',
};

function formatEvidence(value, unit) {
  const number = Number(value);
  const formatted = Number.isFinite(number)
    ? number.toLocaleString('th-TH', { maximumFractionDigits: 2 }) : String(value);
  return unit ? `${formatted} ${unit}` : formatted;
}
