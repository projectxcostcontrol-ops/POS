import { useEffect, useState } from 'react';
import { useStore } from '../store/StoreContext';
import { api } from '../api/client';

/**
 * Counting a kitchen takes longer than one sitting, so entries save as
 * they're typed and the session stays open until someone closes it.
 * Nothing reaches the ledger before that: a half-finished count that
 * committed would mark every shelf nobody reached as "counted and correct".
 *
 * The system's own figure is deliberately NOT shown next to each input.
 * Seeing it invites copying it, and a count that agrees with the system by
 * construction measures nothing - which is worse than no count at all,
 * because the report that follows looks just as confident.
 */
export default function StockCount() {
  const { storeId } = useStore();
  const [materials, setMaterials] = useState([]);
  const [session, setSession] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [values, setValues] = useState({});
  const [saving, setSaving] = useState({});
  const [search, setSearch] = useState('');
  const [hideDone, setHideDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!storeId) return;
    api.getMaterials(storeId).then(setMaterials);
    api.listCounts(storeId).then(setSessions).catch(() => setSessions([]));
    api.getOpenCount(storeId).then((s) => {
      if (s && s.id) {
        setSession(s);
        setValues(s.entries || {});
      }
    }).catch(() => {});
  }, [storeId]);

  if (!storeId) return <p>เลือกสาขาในหน้าตั้งค่าก่อน</p>;

  async function start() {
    setBusy(true);
    try {
      const s = await api.startCount(storeId);
      setSession(s);
      setValues(s.entries || {});
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function saveEntry(materialId, raw) {
    const text = String(raw).trim();
    setValues((v) => ({ ...v, [materialId]: text }));
    if (text === '') {
      await api.clearCountEntry(storeId, session.id, materialId).catch(() => {});
      return;
    }
    const num = Number(text);
    if (Number.isNaN(num) || num < 0) return;
    setSaving((s) => ({ ...s, [materialId]: true }));
    try {
      await api.setCountEntry(storeId, session.id, materialId, num);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving((s) => ({ ...s, [materialId]: false }));
    }
  }

  async function close() {
    if (!window.confirm(
      'ปิดรอบนับ? ตัวเลขที่นับจะถูกบันทึกเข้าระบบและใช้เป็นจุดเริ่มของรอบถัดไป')) return;
    setBusy(true);
    setError('');
    try {
      await api.closeCount(storeId, session.id);
      setSession(null);
      setValues({});
      setSessions(await api.listCounts(storeId));
      setMaterials(await api.getMaterials(storeId));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const counted = Object.keys(values).filter((k) => String(values[k]).trim() !== '').length;
  const visible = materials.filter((m) => {
    if (search && !m.name.toLowerCase().includes(search.toLowerCase())) return false;
    if (hideDone && String(values[m.id] ?? '').trim() !== '') return false;
    return true;
  });
  const lastClosed = sessions.find((s) => s.status === 'closed');

  if (!session) {
    return (
      <div>
        <p style={{ fontSize: 15, fontWeight: 500, margin: '0 0 4px' }}>นับสต๊อก</p>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '0 0 16px' }}>
          นับของจริงในครัวแล้วบันทึก เพื่อเทียบกับที่ระบบคำนวณไว้ — เป็นข้อมูลตั้งต้นของหน้าวิเคราะห์ส่วนต่าง
        </p>

        <div className="card" style={{ marginBottom: 16 }}>
          <p style={{ fontSize: 13, margin: '0 0 12px' }}>
            {lastClosed
              ? `รอบล่าสุด: ${formatDate(lastClosed.closed_at)}`
              : 'ยังไม่เคยนับสต๊อก — รอบแรกจะเป็นจุดตั้งต้น ยังเทียบส่วนต่างไม่ได้จนกว่าจะนับรอบที่สอง'}
          </p>
          <button onClick={start} disabled={busy} style={{ background: 'var(--surface-1)' }}>
            {busy ? 'กำลังเปิด...' : 'เริ่มนับรอบใหม่'}
          </button>
        </div>

        {sessions.filter((s) => s.status === 'closed').length > 0 && (
          <div className="card">
            <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 12px' }}>ประวัติการนับ</p>
            {sessions.filter((s) => s.status === 'closed').map((s, idx, arr) => (
              <div key={s.id} style={{
                display: 'flex', alignItems: 'center', gap: 8, padding: '8px 0',
                borderBottom: idx < arr.length - 1 ? '0.5px solid var(--border)' : 'none',
              }}>
                <span style={{ flex: 1, fontSize: 13 }}>{formatDate(s.closed_at)}</span>
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  {Object.keys(s.entries || {}).length} รายการ
                </span>
              </div>
            ))}
          </div>
        )}

        {error && <p style={{ fontSize: 12, color: 'var(--text-danger)', marginTop: 12 }}>{error}</p>}
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
        <div>
          <p style={{ fontSize: 15, fontWeight: 500, margin: '0 0 2px' }}>กำลังนับ</p>
          <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: 0 }}>
            เริ่ม {formatDate(session.started_at)} · บันทึกอัตโนมัติ · นับแล้ว {counted}/{materials.length}
          </p>
        </div>
        <button onClick={close} disabled={busy || counted === 0}
          style={{ background: 'var(--surface-1)', whiteSpace: 'nowrap' }}>
          {busy ? 'กำลังปิด...' : 'ปิดรอบนับ'}
        </button>
      </div>

      {error && <p style={{ fontSize: 12, color: 'var(--text-danger)', marginBottom: 12 }}>{error}</p>}

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12 }}>
        <input value={search} onChange={(e) => setSearch(e.target.value)}
          placeholder="ค้นหาวัตถุดิบ" style={{ flex: 1 }} />
        <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap' }}>
          <input type="checkbox" checked={hideDone} onChange={() => setHideDone(!hideDone)} />
          ซ่อนที่นับแล้ว
        </label>
      </div>

      <div className="card">
        {visible.length === 0 && (
          <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: 0 }}>
            {hideDone ? 'นับครบทุกรายการแล้ว' : 'ไม่พบวัตถุดิบที่ค้นหา'}
          </p>
        )}
        {visible.map((m, idx) => {
          const val = values[m.id] ?? '';
          const done = String(val).trim() !== '';
          return (
            <div key={m.id} style={{
              display: 'flex', alignItems: 'center', gap: 10, padding: '10px 0',
              borderBottom: idx < visible.length - 1 ? '0.5px solid var(--border)' : 'none',
            }}>
              <span style={{ flex: 1, fontSize: 14 }}>{m.name}</span>
              {saving[m.id] && <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>บันทึก...</span>}
              {done && !saving[m.id] && <span style={{ fontSize: 12, color: 'var(--text-success)' }}>✓</span>}
              <input type="number" value={val} placeholder="นับได้"
                onChange={(e) => saveEntry(m.id, e.target.value)}
                style={{ width: 80, fontSize: 13, textAlign: 'right' }} />
              <span style={{ fontSize: 12, color: 'var(--text-secondary)', minWidth: 36 }}>{m.unit}</span>
            </div>
          );
        })}
      </div>

      <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 12 }}>
        รายการที่ยังไม่ได้กรอกจะไม่ถูกแตะต้องเลยตอนปิดรอบ — นับเท่าที่นับได้ก่อนได้
      </p>
    </div>
  );
}

function formatDate(iso) {
  if (!iso) return '-';
  try {
    return new Date(iso).toLocaleString('th-TH', {
      day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return iso;
  }
}
