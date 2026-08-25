import { useEffect, useState } from 'react';
import { api } from '../api/client';

const baht = (n) => '฿' + Math.abs(Math.round(n)).toLocaleString('en-US');

/**
 * What a finished count found, shown where the counting happens.
 *
 * The caveats are on the card itself, not tucked inside the detail view.
 * A figure like "฿1,240 missing" reads as a measurement, and someone
 * seeing only that will treat it as one - so when the system knows the
 * number is incomplete (no baseline, menus without recipes, manual stock
 * edits absorbing the gap), it has to say so at the same moment it shows
 * the number, not one tap later.
 */
export default function VarianceSummary({ storeId, session }) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!storeId || !session?.id) return;
    setReport(null);
    setError('');
    api.getVariance(storeId, session.id)
      .then(setReport)
      .catch((e) => setError(e.message));
  }, [storeId, session?.id]);

  if (error) {
    return (
      <div className="card" style={{ marginBottom: 16 }}>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: 0 }}>
          ดูผลรอบนี้ไม่ได้: {error}
        </p>
      </div>
    );
  }
  if (!report) {
    return (
      <div className="card" style={{ marginBottom: 16 }}>
        <p style={{ fontSize: 13, margin: 0 }}>กำลังคำนวณ...</p>
      </div>
    );
  }

  const caveats = [];
  if (!report.has_baseline) {
    caveats.push('รอบนับแรก ยังไม่มีรอบก่อนหน้าให้เทียบ — ตัวเลขนี้เป็นการปรับสต๊อกให้ตรง ไม่ใช่ของที่หายไป');
  }
  if (report.offcycle_adjustments > 0) {
    caveats.push(`มีการแก้ไขจำนวนนอกรอบ ${report.offcycle_adjustments} ครั้ง — ของที่หายจริงมีมากกว่าตัวเลขนี้`);
  }
  if (report.unmeasured_menus?.length > 0) {
    caveats.push(`${report.unmeasured_menus.length} เมนูขายแล้วแต่ไม่มีสูตร — วัตถุดิบของเมนูพวกนี้ถูกนับเป็นของหายทั้งที่ไม่ได้หาย`);
  }

  const worst = report.rows.filter((r) => r.variance_qty < 0).slice(0, 3);

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <p style={{
        fontSize: 11, fontWeight: 600, letterSpacing: .6, color: 'var(--text-muted)',
        textTransform: 'uppercase', margin: '0 0 8px',
      }}>ของหายไปไหน</p>

      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 16, marginBottom: 10 }}>
        <div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>มูลค่าที่หายไป</div>
          <div style={{
            fontSize: 24, fontWeight: 700, letterSpacing: -.5, marginTop: 1,
            color: report.summary.shortfall_value > 0
              ? 'var(--text-danger)' : 'var(--text-success)',
          }}>
            {baht(report.summary.shortfall_value)}
          </div>
        </div>
        {report.summary.flagged_count > 0 && (
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>เกินเกณฑ์</div>
            <div style={{ fontSize: 18, fontWeight: 600, marginTop: 3 }}>
              {report.summary.flagged_count} รายการ
            </div>
          </div>
        )}
      </div>

      {worst.length > 0 && (
        <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '0 0 10px' }}>
          {worst.map((r) => `${r.name} −${baht(r.variance_value)}`).join(' · ')}
        </p>
      )}

      {caveats.map((c, i) => (
        <p key={i} style={{
          fontSize: 11, color: 'var(--text-warning)', margin: '0 0 6px',
          lineHeight: 1.5,
        }}>⚠ {c}</p>
      ))}

      <button onClick={() => setOpen(!open)}
        style={{ fontSize: 12, width: '100%', marginTop: 6 }}>
        {open ? 'ซ่อนรายละเอียด' : 'ดูรายละเอียด'}
      </button>

      {open && (
        <div style={{ marginTop: 12 }}>
          <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 8px' }}>
            เกณฑ์เตือน: หายเกิน {report.thresholds.pct}% ของที่ควรใช้ และมูลค่าเกิน ฿{report.thresholds.value}
          </p>

          {report.rows.length === 0 && (
            <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>ไม่มีรายการที่นับในรอบนี้</p>
          )}

          {report.rows.map((r, idx) => (
            <div key={r.material_id} style={{
              padding: '10px 0',
              borderTop: idx > 0 ? '1px solid var(--border)' : 'none',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ flex: 1, fontSize: 13.5 }}>
                  {r.name}
                  {r.flagged && (
                    <span style={{ fontSize: 11, color: 'var(--text-danger)' }}> ⚠</span>
                  )}
                </span>
                <span style={{
                  fontSize: 13, fontWeight: 600,
                  color: r.variance_qty < 0 ? 'var(--text-danger)' : 'var(--text-success)',
                }}>
                  {r.variance_value > 0 ? '+' : '−'}{baht(r.variance_value)}
                </span>
              </div>
              <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '2px 0 0' }}>
                {r.measurable
                  ? `ควรใช้ ${r.expected_usage} ${r.unit} · ${r.variance_qty < 0 ? 'หายเพิ่ม' : 'ใช้น้อยกว่าสูตร'} ${Math.abs(r.variance_qty)} ${r.unit} (${r.variance_pct}%)`
                  : `ไม่มียอดใช้ในช่วงนี้ เทียบเป็น % ไม่ได้ · ต่างจากระบบ ${r.variance_qty} ${r.unit}`}
                {r.recorded_waste > 0 && ` · บันทึกของเสียไว้ ${r.recorded_waste} ${r.unit}`}
              </p>
              {r.variance_qty > 0 && r.measurable && (
                <p style={{ fontSize: 11, color: 'var(--text-secondary)', margin: '2px 0 0' }}>
                  ใช้น้อยกว่าที่สูตรบอก — สูตรอาจใส่ปริมาณไว้เยอะเกินจริง
                </p>
              )}
            </div>
          ))}

          <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 12 }}>
            ส่วนต่างไม่ได้แปลว่ามีคนทำผิด — ที่พบบ่อยที่สุดคือสูตรไม่ตรงกับที่ทำจริง
          </p>
        </div>
      )}
    </div>
  );
}
