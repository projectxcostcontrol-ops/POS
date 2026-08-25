import { useEffect, useState } from 'react';
import { useStore } from '../store/StoreContext';
import { api } from '../api/client';
import SalesChart from '../components/SalesChart';

const PERIODS = [
  { id: 'week', label: 'สัปดาห์นี้' },
  { id: 'month', label: 'เดือนนี้' },
  { id: 'prev', label: 'เดือนก่อน' },
];

function windowFor(period, custom) {
  const now = new Date();
  if (period === 'custom' && custom.from && custom.to) {
    const to = new Date(custom.to);
    to.setHours(23, 59, 59, 999);
    return { from: new Date(custom.from).toISOString(), to: to.toISOString() };
  }
  if (period === 'prev') {
    const start = new Date(now.getFullYear(), now.getMonth() - 1, 1);
    const end = new Date(now.getFullYear(), now.getMonth(), 0, 23, 59, 59);
    return { from: start.toISOString(), to: end.toISOString() };
  }
  const start = new Date(now);
  if (period === 'week') { start.setDate(now.getDate() - 6); }
  else { start.setDate(1); }
  start.setHours(0, 0, 0, 0);
  return { from: start.toISOString(), to: now.toISOString() };
}

const baht = (n) => '฿' + Math.round(n).toLocaleString('en-US');

/**
 * Sales over time, summarised by day.
 *
 * This used to be a flat list of every receipt. The question people
 * actually bring here is "which days went well" - not "what was on bill
 * number 47" - so the day view leads and individual bills are one tap
 * further in, for when something looks wrong and needs checking.
 */
export default function Receipts() {
  const { storeId } = useStore();
  const [period, setPeriod] = useState('week');
  const [custom, setCustom] = useState({ from: '', to: '' });
  const [summary, setSummary] = useState(null);
  const [days, setDays] = useState([]);
  const [openDay, setOpenDay] = useState(null);
  const [dayBills, setDayBills] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const periodLabel = period === 'custom'
    ? 'ช่วงเวลาที่เลือก'
    : PERIODS.find((p) => p.id === period)?.label;

  useEffect(() => {
    if (!storeId) return;
    if (period === 'custom' && !(custom.from && custom.to)) return;

    const { from, to } = windowFor(period, custom);
    setLoading(true);
    setError('');

    Promise.all([
      // `top=0` asks for every sold item. The backend still does the
      // aggregation in the same sales read and ranks by quantity, so the
      // summary card does not need another request or bill-by-bill maths.
      api.getSalesOverview(storeId, from, to, 'day', 0),
      api.getDailySales(storeId, from, to),
    ])
      .then(([s, d]) => { setSummary(s); setDays(d); })
      .catch((e) => { setSummary(null); setDays([]); setError(e.message); })
      .finally(() => setLoading(false));
  }, [storeId, period, custom]);

  function toggleDay(date) {
    if (openDay === date) { setOpenDay(null); return; }
    setOpenDay(date);
    setDayBills([]);
    const from = new Date(date + 'T00:00:00').toISOString();
    const to = new Date(date + 'T23:59:59').toISOString();
    // The saved-sales endpoints summarise; individual bills still come
    // from the POS, which only keeps the last 30 days. A day older than
    // that shows its totals but can't show the bills behind them.
    api.getReceipts(storeId, from).then((rows) => {
      setDayBills(rows.filter((r) => (r.created_at || '').startsWith(date)));
    }).catch(() => setDayBills([]));
  }

  if (!storeId) return <p>เลือกสาขาในหน้าตั้งค่าก่อน</p>;

  return (
    <div>
      <p style={{ fontSize: 15, fontWeight: 500, margin: '0 0 4px' }}>รายการขาย</p>
      <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '0 0 16px' }}>
        สรุปรายวัน กดที่วันไหนเพื่อดูบิลของวันนั้น
      </p>

      <div style={{
        display: 'flex', gap: 4, background: 'var(--surface-1)',
        borderRadius: 9, padding: 3, marginBottom: 12,
      }}>
        {PERIODS.map((p) => (
          <button key={p.id} onClick={() => setPeriod(p.id)}
            style={{
              flex: 1, fontSize: 12, padding: '7px 4px', borderRadius: 7,
              background: period === p.id ? 'var(--surface-2)' : 'transparent',
              fontWeight: period === p.id ? 600 : 500,
            }}>
            {p.label}
          </button>
        ))}
        <button onClick={() => setPeriod('custom')} title="เลือกช่วงเวลา"
          style={{
            flex: 'none', width: 40, fontSize: 14, borderRadius: 7,
            background: period === 'custom' ? 'var(--surface-2)' : 'transparent',
          }}>
          📅
        </button>
      </div>

      {period === 'custom' && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12 }}>
          <input type="date" value={custom.from} max={custom.to || undefined}
            onChange={(e) => setCustom({ ...custom, from: e.target.value })}
            style={{ flex: 1, fontSize: 12, minWidth: 0 }} />
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>ถึง</span>
          <input type="date" value={custom.to} min={custom.from || undefined}
            onChange={(e) => setCustom({ ...custom, to: e.target.value })}
            style={{ flex: 1, fontSize: 12, minWidth: 0 }} />
        </div>
      )}

      {loading && <p style={{ fontSize: 13 }}>กำลังโหลด...</p>}
      {error && <p style={{ fontSize: 12, color: 'var(--text-danger)' }}>{error}</p>}

      {summary && !loading && (
        <>
          <div className="card" style={{
            padding: '16px 17px 10px', marginBottom: 10,
          }}>
            <div style={{
              display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between',
              gap: 16, flexWrap: 'wrap',
            }}>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 500 }}>
                  ยอดขายรวม · {periodLabel}
                </div>
                <div style={{
                  fontSize: 26, lineHeight: 1.2, fontWeight: 700,
                  marginTop: 4, letterSpacing: -.45, color: 'var(--accent)',
                }}>
                  {baht(summary.total)}
                </div>
              </div>
              <div style={{ display: 'flex', gap: 22 }}>
                <Stat label="จำนวนบิล" value={summary.bill_count} />
                <Stat label="เฉลี่ยต่อบิล"
                  value={summary.bill_count
                    ? baht(summary.total / summary.bill_count) : '—'} />
              </div>
            </div>

            <SalesItemsTable items={summary.top_items} />
          </div>

          <div className="card" style={{ padding: '12px 14px 9px', marginBottom: 16 }}>
            <div style={{
              fontSize: 11, color: 'var(--text-muted)', fontWeight: 500,
              marginBottom: 5,
            }}>
              แนวโน้มยอดขาย
            </div>
            <SalesChart points={summary.points} from={summary.from} to={summary.to}
              granularity="day" formatValue={baht} compact />
          </div>

          {days.length === 0 ? (
            <div className="card">
              <p style={{ fontSize: 13, margin: 0 }}>ยังไม่มียอดขายในช่วงนี้</p>
              <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '6px 0 0' }}>
                ถ้าเพิ่งเชื่อมสาขา ลองกด “ซิงก์ตอนนี้” ในหน้าตั้งค่าก่อน
              </p>
            </div>
          ) : (
            <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
              {days.map((d, idx) => (
                <div key={d.date} style={{
                  borderTop: idx > 0 ? '1px solid var(--border)' : 'none',
                }}>
                  <div onClick={() => toggleDay(d.date)} style={{
                    display: 'flex', alignItems: 'center', gap: 12,
                    padding: '13px 14px', cursor: 'pointer',
                  }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <p style={{ fontSize: 14, margin: 0, fontWeight: 500 }}>
                        {new Date(d.date + 'T00:00:00').toLocaleDateString('th-TH',
                          { weekday: 'short', day: 'numeric', month: 'short' })}
                      </p>
                      <p style={{ fontSize: 11.5, color: 'var(--text-muted)', margin: '2px 0 0' }}>
                        {d.bill_count} บิล
                      </p>
                    </div>
                    <span style={{ fontSize: 14, fontWeight: 600 }}>{baht(d.total)}</span>
                    <span style={{ color: 'var(--text-muted)', flex: 'none' }}>
                      {openDay === d.date ? '⌄' : '›'}
                    </span>
                  </div>

                  {openDay === d.date && (
                    <div style={{ background: 'var(--surface-1)', padding: '4px 14px 12px' }}>
                      {dayBills.length === 0 ? (
                        <p style={{ fontSize: 11.5, color: 'var(--text-muted)', margin: '8px 0 0' }}>
                          ดูบิลรายใบไม่ได้ — Loyverse เก็บบิลย้อนหลังได้ 30 วัน
                          ยอดรวมด้านบนยังถูกต้อง เพราะบันทึกไว้ตั้งแต่ตอนซิงก์
                        </p>
                      ) : dayBills.map((b) => (
                        <div key={b.receipt_number} style={{
                          padding: '9px 0', borderBottom: '1px solid var(--border)',
                        }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                            <span style={{ fontSize: 12.5, fontWeight: 500 }}>
                              #{b.receipt_number}
                            </span>
                            <span style={{ fontSize: 12.5 }}>{baht(b.total || 0)}</span>
                          </div>
                          <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '3px 0 0' }}>
                            {(b.line_items || []).map((li) =>
                              `${li.item_name} x${li.quantity}`).join(' · ')}
                          </p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Stat({ label, value, big }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 500 }}>{label}</div>
      <div style={{ fontSize: big ? 22 : 16, fontWeight: 700, marginTop: 2, letterSpacing: -.3 }}>
        {value}
      </div>
    </div>
  );
}

function SalesItemsTable({ items }) {
  const rows = [...(items || [])].sort((a, b) =>
    (b.qty || 0) - (a.qty || 0) || (b.revenue || 0) - (a.revenue || 0));

  return (
    <div style={{ marginTop: 15, borderTop: '1px solid var(--border)' }}>
      <div style={{
        display: 'grid', gridTemplateColumns: '28px minmax(0, 1fr) 58px 82px',
        gap: 8, padding: '9px 0 7px', fontSize: 10.5,
        color: 'var(--text-muted)', fontWeight: 600,
      }}>
        <span style={{ textAlign: 'center' }}>#</span>
        <span>รายการที่ขาย</span>
        <span style={{ textAlign: 'right' }}>จำนวน</span>
        <span style={{ textAlign: 'right' }}>ยอดขาย</span>
      </div>

      {rows.length === 0 ? (
        <p style={{
          fontSize: 11.5, color: 'var(--text-muted)',
          textAlign: 'center', margin: 0, padding: '10px 0 7px',
        }}>
          ยังไม่มีรายการสินค้าที่ขายในช่วงนี้
        </p>
      ) : (
        <div style={{ maxHeight: 292, overflowY: 'auto' }}>
          {rows.map((item, index) => (
            <div key={`${item.name}-${index}`} style={{
              display: 'grid', gridTemplateColumns: '28px minmax(0, 1fr) 58px 82px',
              gap: 8, alignItems: 'center', padding: '9px 0',
              borderTop: '1px solid var(--border)',
            }}>
              <span style={{
                textAlign: 'center', fontSize: 12, fontWeight: 600,
                color: index === 0 ? 'var(--accent)' : 'var(--text-muted)',
              }}>
                {index + 1}
              </span>
              <span style={{
                minWidth: 0, fontSize: 12.5, fontWeight: 500,
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
              }} title={item.name}>
                {item.name}
              </span>
              <span style={{ textAlign: 'right', fontSize: 12.5, fontWeight: 600 }}>
                {formatQuantity(item.qty)}
              </span>
              <span style={{ textAlign: 'right', fontSize: 12, color: 'var(--text-muted)' }}>
                {baht(item.revenue || 0)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function formatQuantity(value) {
  const n = Number(value) || 0;
  return Number.isInteger(n) ? n.toLocaleString('en-US') : n.toLocaleString('en-US', {
    maximumFractionDigits: 2,
  });
}
