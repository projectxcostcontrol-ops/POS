import { useEffect, useState } from 'react';
import { useStore } from '../store/StoreContext';
import { api } from '../api/client';

/**
 * The gap between what recipes predicted and what a count actually found.
 *
 * The screen's main job is to stay honest about its own limits: it says
 * when there's no baseline to measure from, which materials had no usage
 * to compare against, and which menus sold without a recipe. That last one
 * matters most - those sales consumed ingredients nothing recorded, so
 * they show up here as losses that were never really losses.
 */
export default function Variance() {
  const { storeId } = useStore();
  const [sessions, setSessions] = useState([]);
  const [selected, setSelected] = useState('');
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [showUnmeasured, setShowUnmeasured] = useState(false);

  useEffect(() => {
    if (!storeId) return;
    api.listCounts(storeId).then((list) => {
      const closed = list.filter((s) => s.status === 'closed');
      setSessions(closed);
      if (closed.length) setSelected(closed[0].id);
    }).catch((e) => setError(e.message));
  }, [storeId]);

  useEffect(() => {
    if (!storeId || !selected) return;
    setLoading(true);
    setError('');
    api.getVariance(storeId, selected)
      .then(setReport)
      .catch((e) => { setReport(null); setError(e.message); })
      .finally(() => setLoading(false));
  }, [storeId, selected]);

  if (!storeId) return <p>เลือกสาขาในหน้าตั้งค่าก่อน</p>;

  if (sessions.length === 0) {
    return (
      <div>
        <p style={{ fontSize: 15, fontWeight: 500, margin: '0 0 4px' }}>วิเคราะห์ส่วนต่าง</p>
        <div className="card" style={{ marginTop: 12 }}>
          <p style={{ fontSize: 13, margin: 0 }}>
            ยังไม่มีรอบนับสต๊อกที่ปิดแล้ว — ไปที่หน้า "นับสต๊อก" เพื่อนับรอบแรกก่อน
          </p>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '8px 0 0' }}>
            ระบบเทียบได้เฉพาะกับของที่นับจริงเท่านั้น ถ้าไม่นับ ตัวเลขในระบบก็คือตัวเลขที่คำนวณจากสูตร
            ซึ่งเทียบกับตัวเองแล้วตรงเสมอ
          </p>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
        <div>
          <p style={{ fontSize: 15, fontWeight: 500, margin: '0 0 4px' }}>วิเคราะห์ส่วนต่าง</p>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: 0 }}>
            เทียบของที่นับได้จริง กับที่ระบบคำนวณไว้จากสูตรและยอดขาย
          </p>
        </div>
        <select value={selected} onChange={(e) => setSelected(e.target.value)} style={{ fontSize: 12 }}>
          {sessions.map((s) => (
            <option key={s.id} value={s.id}>รอบ {formatDate(s.closed_at)}</option>
          ))}
        </select>
      </div>

      {loading && <p style={{ fontSize: 13 }}>กำลังคำนวณ...</p>}
      {error && <p style={{ fontSize: 12, color: 'var(--text-danger)' }}>{error}</p>}

      {report && (
        <>
          {!report.has_baseline && (
            <div style={{
              background: '#fdf3e3', border: '1px solid var(--text-warning)', borderRadius: 8,
              padding: '10px 12px', marginBottom: 12, fontSize: 12, color: 'var(--text-warning)',
            }}>
              นี่คือรอบนับแรก — ยังไม่มีรอบก่อนหน้าให้เทียบ ตัวเลขด้านล่างจึงเป็นแค่การปรับสต๊อกให้ตรงความจริง
              ไม่ใช่ส่วนต่างของช่วงเวลา รอบถัดไปถึงจะวิเคราะห์ได้จริง
            </div>
          )}

          {report.unmeasured_menus.length > 0 && (
            <div style={{
              background: '#fdf3e3', border: '1px solid var(--text-warning)', borderRadius: 8,
              padding: '10px 12px', marginBottom: 12, fontSize: 12, color: 'var(--text-warning)',
            }}>
              <div>
                ⚠ {report.unmeasured_menus.length} เมนูขายไปแล้วแต่ยังไม่ผูกสูตร —
                วัตถุดิบที่ใช้กับเมนูพวกนี้จะโผล่มาเป็น "ของหาย" ทั้งที่ไม่ได้หาย
                ตัวเลขด้านล่างจึงยังไม่ครบ
              </div>
              <button onClick={() => setShowUnmeasured(!showUnmeasured)}
                style={{ background: 'none', padding: 0, fontSize: 11, color: 'var(--accent)', marginTop: 4 }}>
                {showUnmeasured ? 'ซ่อน' : 'ดูว่าเมนูไหน'}
              </button>
              {showUnmeasured && (
                <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                  {report.unmeasured_menus.map((m) => <li key={m}>{m}</li>)}
                </ul>
              )}
            </div>
          )}

          {report.offcycle_adjustments > 0 && (
            <div style={{
              background: '#fdf3e3', border: '1px solid var(--text-warning)', borderRadius: 8,
              padding: '10px 12px', marginBottom: 12, fontSize: 12, color: 'var(--text-warning)',
            }}>
              ⚠ มีการ "แก้ไขจำนวน" จากหน้าวัตถุดิบ {report.offcycle_adjustments} ครั้งในช่วงนี้ —
              การแก้แต่ละครั้งกลบส่วนต่างที่สะสมอยู่ไปแล้วบางส่วน ตัวเลขด้านล่างจึงเป็น
              <b>อย่างน้อยที่สุด</b> ของที่หายไปจริง ไม่ใช่ตัวเลขที่วัดได้ครบ
            </div>
          )}

          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
            <StatCard label="มูลค่าที่หายไป" value={`฿${report.summary.shortfall_value.toLocaleString()}`} />
            <StatCard label="เกินเกณฑ์" value={report.summary.flagged_count} />
            <StatCard label="เทียบไม่ได้" value={report.summary.unmeasurable_count} />
          </div>

          <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 8px' }}>
            เกณฑ์เตือน: หายเกิน {report.thresholds.pct}% ของที่ควรใช้ และมูลค่าเกิน ฿{report.thresholds.value}
            {report.previous_closed_at && ` · ช่วง ${formatDate(report.previous_closed_at)} → ${formatDate(report.session.closed_at)}`}
          </p>

          <div className="card">
            {report.rows.length === 0 && (
              <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: 0 }}>ไม่มีรายการที่นับในรอบนี้</p>
            )}
            {report.rows.map((r, idx) => (
              <div key={r.material_id} style={{
                padding: '12px 0',
                borderBottom: idx < report.rows.length - 1 ? '0.5px solid var(--border)' : 'none',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ flex: 1, fontSize: 14 }}>
                    {r.name}
                    {r.flagged && <span style={{ fontSize: 11, color: 'var(--text-danger)' }}> ⚠ เกินเกณฑ์</span>}
                  </span>
                  <span style={{
                    fontSize: 14, fontWeight: 500,
                    color: r.variance_qty < 0 ? 'var(--text-danger)' : 'var(--text-success)',
                  }}>
                    {r.variance_value > 0 ? '+' : ''}฿{Math.abs(r.variance_value).toLocaleString()}
                  </span>
                </div>
                <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '2px 0 0' }}>
                  {r.measurable
                    ? `ควรใช้ ${r.expected_usage} ${r.unit} · ${r.variance_qty < 0 ? 'หายเพิ่ม' : 'ใช้น้อยกว่าสูตร'} ${Math.abs(r.variance_qty)} ${r.unit} (${r.variance_pct}%)`
                    : `ไม่มียอดใช้ในช่วงนี้ เทียบเป็นเปอร์เซ็นต์ไม่ได้ · ต่างจากระบบ ${r.variance_qty} ${r.unit}`}
                  {r.recorded_waste > 0 && ` · บันทึกของเสียไว้ ${r.recorded_waste} ${r.unit}`}
                </p>
                {r.variance_qty > 0 && r.measurable && (
                  <p style={{ fontSize: 11, color: 'var(--text-secondary)', margin: '2px 0 0' }}>
                    ใช้น้อยกว่าที่สูตรบอก — สูตรอาจใส่ปริมาณไว้เยอะเกินจริง
                  </p>
                )}
              </div>
            ))}
          </div>

          <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 12 }}>
            ส่วนต่างไม่ได้แปลว่ามีคนทำผิด — ที่พบบ่อยที่สุดคือสูตรไม่ตรงกับที่ทำจริง
            ลองเทียบกับปริมาณที่ครัวใช้จริงก่อนสรุป
          </p>
        </>
      )}
    </div>
  );
}

function StatCard({ label, value }) {
  return (
    <div className="card" style={{ flex: '1 1 130px', minWidth: 130 }}>
      <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 4px' }}>{label}</p>
      <p style={{ fontSize: 22, fontWeight: 500, margin: 0 }}>{value}</p>
    </div>
  );
}

function formatDate(iso) {
  if (!iso) return '-';
  try {
    return new Date(iso).toLocaleDateString('th-TH', { day: 'numeric', month: 'short', year: 'numeric' });
  } catch {
    return iso;
  }
}
