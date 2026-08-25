import { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { useStore } from '../store/StoreContext';
import { useAuth } from '../auth/AuthContext';
import { api } from '../api/client';
import SalesChart from '../components/SalesChart';
import {
  ArrowClockwise, CalendarBlank, CaretRight, CheckCircle, ClipboardText,
  CookingPot, Package, ShoppingCart, WarningCircle,
} from '@phosphor-icons/react';

const PERIODS = [
  { id: 'day', label: 'วันนี้' },
  { id: 'week', label: 'สัปดาห์นี้' },
  { id: 'month', label: 'เดือนนี้' },
];

function windowFor(period, custom) {
  const now = new Date();
  if (period === 'custom' && custom.from && custom.to) {
    const to = new Date(custom.to);
    to.setHours(23, 59, 59, 999);
    return { from: new Date(custom.from).toISOString(), to: to.toISOString() };
  }
  const start = new Date(now);
  if (period === 'day') start.setHours(0, 0, 0, 0);
  else if (period === 'week') { start.setDate(now.getDate() - 6); start.setHours(0, 0, 0, 0); }
  else { start.setDate(1); start.setHours(0, 0, 0, 0); }
  return { from: start.toISOString(), to: now.toISOString() };
}

const baht = (n) => '฿' + Math.round(n).toLocaleString('en-US');

export default function Dashboard() {
  const { storeId } = useStore();
  const { can, profile } = useAuth();
  const showMoney = can('view_money');

  const [alerts, setAlerts] = useState(null);
  const [period, setPeriod] = useState('day');
  const [custom, setCustom] = useState({ from: '', to: '' });
  const [summary, setSummary] = useState(null);
  const [top, setTop] = useState([]);
  const [loading, setLoading] = useState(false);
  const [salesError, setSalesError] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [refreshNote, setRefreshNote] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);

  // Alerts load independently of the sales figures. If the sales endpoints
  // fail, staff should still see that stock is running out - the two
  // aren't related and shouldn't fail together.
  useEffect(() => {
    if (!storeId) return;
    api.getAlerts(storeId).then(setAlerts).catch(() => setAlerts(null));
  }, [storeId, reloadKey]);

  useEffect(() => {
    if (!storeId || !showMoney) return;
    if (period === 'custom' && !(custom.from && custom.to)) return;

    const { from, to } = windowFor(period, custom);
    const granularity = period === 'day' ? 'hour' : 'day';
    setLoading(true);
    setSalesError('');

    api.getSalesOverview(storeId, from, to, granularity, 5)
      .then((s) => { setSummary(s); setTop(s.top_items || []); })
      .catch((e) => { setSummary(null); setSalesError(e.message); })
      .finally(() => setLoading(false));
  }, [storeId, period, custom, showMoney, reloadKey]);

  async function refreshAll() {
    setRefreshing(true);
    setRefreshNote(null);
    try {
      await api.sync(storeId);

      // Verify rather than assume. A sync that returns without error can
      // still have missed receipts a till uploaded late, and the only way
      // to know is to compare counts against the POS.
      let check = await api.reconcileSales(storeId, 1);
      if (check.missing_count > 0) {
        await api.repairSales(storeId);
        check = await api.reconcileSales(storeId, 1);
      }

      setRefreshNote(check.missing_count > 0
        ? { ok: false, text: `ยังขาด ${check.missing_count} บิล — Loyverse อาจยังส่งข้อมูลไม่ครบ ลองอีกครั้งในสักครู่` }
        : { ok: true, text: `อัปเดตแล้ว · ตรงกับ Loyverse ${check.pos.count} บิล` });

      setReloadKey((k) => k + 1);
    } catch (e) {
      setRefreshNote({ ok: false, text: `อัปเดตไม่สำเร็จ: ${e.message}` });
    } finally {
      setRefreshing(false);
    }
  }

  if (!storeId) return <p>เลือกสาขาในหน้าตั้งค่าก่อน</p>;

  return (
    <div className="dashboard-page">
      <div className="page-header dashboard-header">
        <div>
          <p className="eyebrow">{profile.business_name || 'ล้านครัว'}</p>
          <h1>ภาพรวมวันนี้</h1>
          <p className="page-subtitle">
          {new Date().toLocaleDateString('th-TH',
            { weekday: 'long', day: 'numeric', month: 'long' })}
          </p>
        </div>
        {/* One button that does the whole job: pull anything new from the
            POS, check nothing was missed, and repair it if it was. The
            old version made someone open Settings, press "ตรวจสอบ", read
            two numbers, decide they disagreed, then press a second
            button - a diagnostic workflow handed to a shop owner. */}
        <button className="button-primary" onClick={refreshAll} disabled={refreshing}>
          <ArrowClockwise size={19} className={refreshing ? 'spin' : ''} />
          {refreshing ? 'กำลังอัปเดต...' : 'อัปเดตข้อมูล'}
        </button>
      </div>

      {refreshNote && (
        <p style={{
          fontSize: 13, margin: '-10px 2px 18px',
          color: refreshNote.ok ? 'var(--text-success)' : 'var(--text-warning)',
        }}>
          {refreshNote.text}
        </p>
      )}

      <Alerts alerts={alerts} />

      {showMoney ? (
        <>
          <p className="section-label">ยอดขาย</p>

          <PeriodPicker period={period} setPeriod={setPeriod}
            custom={custom} setCustom={setCustom} />

          {loading && <p style={{ fontSize: 13 }}>กำลังโหลด...</p>}
          {salesError && (
            <p style={{ fontSize: 12, color: 'var(--text-danger)' }}>{salesError}</p>
          )}

          {summary && !loading && (
            <div className="dashboard-sales-grid">
              <SummaryCard summary={summary} period={period} />
              <TopItems items={top} />
            </div>
          )}
        </>
      ) : (
        <StaffShortcuts />
      )}
    </div>
  );
}

function Alerts({ alerts }) {
  if (!alerts) return null;

  const rows = [];
  if (alerts.negative_stock?.length) {
    rows.push({
      to: '/materials', icon: WarningCircle, level: 'danger',
      title: `สต๊อกติดลบ ${alerts.negative_stock.length} อย่าง`,
      sub: alerts.negative_stock.slice(0, 3).map((m) => m.name).join(' · ') +
        ' — ตรวจสอบว่าลืมบันทึกของที่รับเข้ามาหรือไม่',
    });
  }
  if (alerts.low_stock?.length) {
    rows.push({
      to: '/materials', icon: Package, level: 'danger',
      title: `ของใกล้หมด ${alerts.low_stock.length} อย่าง`,
      sub: alerts.low_stock.slice(0, 3).map((m) => m.name).join(' · '),
    });
  }
  if (alerts.pending_drafts > 0) {
    rows.push({
      to: '/receiving', icon: ClipboardText, level: 'warn',
      title: `ใบส่งของรอตรวจ ${alerts.pending_drafts} ใบ`,
      sub: 'รอดำเนินการ เพื่อปรับสต๊อก',
    });
  }
  if (alerts.count_due) {
    rows.push({
      to: '/stock-count', icon: CheckCircle, level: 'warn',
      title: 'ถึงรอบตรวจนับสต๊อก',
      // "never counted" and "counted a while ago" are different situations:
      // one is a setup step nobody has done, the other a habit that slipped.
      sub: alerts.days_since_count === null
        ? 'ยังไม่เคยนับ — นับครั้งแรกเพื่อเริ่มเทียบของหาย'
        : `นับครั้งล่าสุด ${alerts.days_since_count} วันที่แล้ว`,
    });
  }

  return (
    <section className="alerts-section">
      <p className="section-label">ต้องจัดการ</p>

      {rows.length === 0 ? (
        <div style={{
          background: 'var(--surface-1)', border: '1px dashed var(--border)',
          borderRadius: 10, padding: 16, textAlign: 'center',
          fontSize: 13, color: 'var(--text-success)',
        }}>
          วันนี้เรียบร้อยดี ✓
        </div>
      ) : rows.map((r, i) => {
        const Icon = r.icon;
        return (
        <NavLink key={i} to={r.to} className={`alert-row ${r.level}`}>
          <span className="alert-icon"><Icon size={22} weight="regular" /></span>
          <span style={{ minWidth: 0 }}>
            <span style={{ fontSize: 14, fontWeight: 600, display: 'block' }}>{r.title}</span>
            <span style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>{r.sub}</span>
          </span>
          <CaretRight className="alert-caret" size={19} />
        </NavLink>
      );})}
    </section>
  );
}

function PeriodPicker({ period, setPeriod, custom, setCustom }) {
  return (
    <>
      <div className="period-picker">
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
        <button onClick={() => setPeriod('custom')} title="เลือกช่วงเวลา" aria-label="เลือกช่วงเวลา"
          style={{
            flex: 'none', width: 40, fontSize: 14, borderRadius: 7,
            background: period === 'custom' ? 'var(--surface-2)' : 'transparent',
          }}>
          <CalendarBlank size={18} />
        </button>
      </div>

      {period === 'custom' && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12 }}>
          {/* The browser's own date picker - on a phone this is the native
              iOS/Android wheel, which is more familiar than anything we'd
              build and works without any extra code. */}
          <input type="date" value={custom.from} max={custom.to || undefined}
            onChange={(e) => setCustom({ ...custom, from: e.target.value })}
            style={{ flex: 1, fontSize: 12, minWidth: 0 }} />
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>ถึง</span>
          <input type="date" value={custom.to} min={custom.from || undefined}
            onChange={(e) => setCustom({ ...custom, to: e.target.value })}
            style={{ flex: 1, fontSize: 12, minWidth: 0 }} />
        </div>
      )}
    </>
  );
}

function SummaryCard({ summary, period }) {
  const hourly = period === 'day';

  return (
    <section className="sales-summary-column">
      <div className="card" style={{ padding: '15px 14px 10px', marginBottom: 9 }}>
        <div style={{
          display: 'flex', alignItems: 'flex-end',
          justifyContent: 'space-between', marginBottom: 12,
        }}>
          <div>
            <div style={{ fontSize: 11.5, color: 'var(--text-muted)', fontWeight: 500 }}>
              ยอดขายรวม
            </div>
            <div style={{ fontSize: 25, fontWeight: 700, letterSpacing: -.5, marginTop: 1 }}>
              {baht(summary.total)}
            </div>
          </div>
          {/* No comparison when there's no previous period to compare with -
              a made-up 0% would read as a real result. */}
          {summary.compare && (
            <span style={{
              fontSize: 11.5, fontWeight: 600, padding: '3px 8px', borderRadius: 20,
              color: summary.compare.up ? 'var(--text-success)' : 'var(--text-danger)',
              background: 'var(--surface-1)',
            }}>
              {summary.compare.up ? '↑' : '↓'} {summary.compare.pct}%
            </span>
          )}
        </div>

        <SalesChart points={summary.points} from={summary.from} to={summary.to}
          granularity={hourly ? 'hour' : 'day'} formatValue={baht} />
      </div>

      <div style={{ display: 'flex', gap: 9, marginBottom: 4 }}>
        <div className="card" style={{ flex: 1, padding: '11px 12px' }}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 500 }}>
            กำไรขั้นต้น
          </div>
          <div style={{ fontSize: 19, fontWeight: 700, marginTop: 2, color: 'var(--text-success)' }}>
            {baht(summary.gross_profit)}
          </div>
        </div>
        <div className="card" style={{ flex: 1, padding: '11px 12px' }}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 500 }}>
            จำนวนบิล
          </div>
          <div style={{ fontSize: 19, fontWeight: 700, marginTop: 2 }}>
            {summary.bill_count}
          </div>
        </div>
      </div>

      {/* Menus with no recipe contribute revenue but no cost, so profit
          reads higher than it is. Saying so beats a confident wrong number. */}
      {summary.uncosted_menus?.length > 0 && (
        <p style={{ fontSize: 11, color: 'var(--text-warning)', margin: '8px 2px 0' }}>
          ⚠ {summary.uncosted_menus.length} เมนูยังไม่ผูกสูตร
          จึงคิดต้นทุนไม่ได้ — กำไรขั้นต้นจริงจะน้อยกว่านี้{' '}
          <NavLink to="/recipes" style={{ color: 'var(--accent)' }}>ผูกสูตร</NavLink>
        </p>
      )}
    </section>
  );
}

function TopItems({ items }) {
  const rows = items || [];
  const top = rows[0]?.qty || 1;

  return (
    <section className="top-items-column">
      <p className="section-label">เมนูขายดี</p>

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{
          display: 'flex', gap: 11, padding: '8px 13px',
          background: 'var(--surface-1)', fontSize: 10.5,
          color: 'var(--text-muted)', fontWeight: 600,
        }}>
          <span style={{ width: 19, textAlign: 'center' }}>#</span>
          <span style={{ flex: 1 }}>เมนู</span>
          <span style={{ width: 46, textAlign: 'right' }}>จาน</span>
          <span style={{ width: 62, textAlign: 'right' }}>ยอดเงิน</span>
        </div>

        {rows.length === 0 ? (
          <div className="top-items-empty">
            <CookingPot size={28} weight="light" />
            <span>ยังไม่มียอดขายในช่วงนี้</span>
            <small>เมื่อมีรายการขาย เมนูขายดีจะแสดงที่นี่</small>
          </div>
        ) : rows.map((m, i) => (
          <div key={m.name} style={{
            display: 'flex', alignItems: 'center', gap: 11, padding: '11px 13px',
            borderTop: '1px solid var(--border)',
          }}>
            <span style={{
              width: 19, textAlign: 'center', fontSize: 14, fontWeight: 700,
              color: i === 0 ? 'var(--accent)' : 'var(--text-muted)',
            }}>{i + 1}</span>
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{
                fontSize: 13.5, fontWeight: 500, display: 'block',
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
              }}>{m.name}</span>
              {/* The bar shows the gap between ranks - knowing #1 sold twice
                  what #2 did is more useful than knowing it came first. */}
              <span style={{
                display: 'block', height: 4, borderRadius: 2,
                background: 'var(--surface-1)', marginTop: 5, overflow: 'hidden',
              }}>
                <i style={{
                  display: 'block', height: '100%', borderRadius: 2,
                  background: 'var(--accent)', opacity: .55,
                  width: `${Math.round((m.qty / top) * 100)}%`,
                }} />
              </span>
            </span>
            <span style={{ width: 46, textAlign: 'right', fontSize: 13, fontWeight: 600 }}>
              {m.qty}
            </span>
            <span style={{ width: 62, textAlign: 'right', fontSize: 12, color: 'var(--text-muted)' }}>
              {baht(m.revenue)}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

function StaffShortcuts() {
  const links = [
    { to: '/materials', icon: Package, label: 'ของในครัว' },
    { to: '/receiving', icon: ShoppingCart, label: 'ซื้อของเข้าร้าน' },
    { to: '/stock-count', icon: ClipboardText, label: 'นับของ' },
    { to: '/recipes', icon: CookingPot, label: 'สูตรอาหาร' },
  ];

  return (
    <>
      <p className="section-label">ทำอะไรได้บ้าง</p>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 9 }}>
        {links.map((l) => {
          const Icon = l.icon;
          return (
          <NavLink key={l.to} to={l.to} style={{
            textDecoration: 'none', color: 'inherit',
            background: 'var(--surface-2)', border: '1px solid var(--border)',
            borderRadius: 10, padding: '18px 14px', textAlign: 'center',
          }}>
            <Icon size={26} weight="regular" />
            <div style={{ fontSize: 13, fontWeight: 500, marginTop: 6 }}>{l.label}</div>
          </NavLink>
        );})}
      </div>
    </>
  );
}
