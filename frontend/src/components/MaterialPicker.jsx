import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';

/**
 * Pick a material by typing, or create one without leaving the form.
 *
 * A plain <select> stops working somewhere around thirty ingredients -
 * finding "น้ำมันหอย" means scrolling a list nobody sorted. Worse, when
 * the ingredient simply isn't there yet, a select offers no way forward:
 * the delivery note is in hand, the item is real, and the only option is
 * to abandon the form, go create the material, and start over. Most
 * people give up and file it under whatever is closest, which quietly
 * corrupts the stock they came here to record.
 */
export default function MaterialPicker({ materials, value, onChange, storeId, onCreated }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [creating, setCreating] = useState(false);
  const boxRef = useRef(null);

  const selected = materials.find((m) => m.id === value);

  useEffect(() => {
    if (!open) return;
    const close = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  const q = query.trim().toLowerCase();
  const matches = q
    ? materials.filter((m) => m.name.toLowerCase().includes(q))
    : materials;

  // Only offer creation when the typed name isn't already an exact match,
  // so nobody ends up with two materials called the same thing.
  const exact = materials.some((m) => m.name.trim().toLowerCase() === q);
  const canCreate = q.length > 0 && !exact;

  async function createMaterial() {
    const name = query.trim();
    const unit = window.prompt(`หน่วยของ "${name}" (เช่น กก., ขวด, ฟอง)`, '');
    if (!unit) return;

    setCreating(true);
    try {
      const id = `mat_${Date.now()}`;
      // No cost here on purpose - unit cost comes from what this very
      // delivery charged, which the row's price field is about to record.
      await api.upsertMaterial(storeId, id, {
        name, unit: unit.trim(), par_level: 0,
      });
      await onCreated();     // refresh the list in the parent
      onChange(id);
      setOpen(false);
      setQuery('');
    } catch (e) {
      window.alert(`สร้างวัตถุดิบไม่สำเร็จ: ${e.message}`);
    } finally {
      setCreating(false);
    }
  }

  return (
    <div ref={boxRef} style={{ position: 'relative', flex: 1.2, minWidth: 0 }}>
      <button type="button" onClick={() => { setOpen(!open); setQuery(''); }}
        style={{
          width: '100%', fontSize: 12, textAlign: 'left', padding: '8px 10px',
          background: 'var(--surface-2)', border: '1px solid var(--border)',
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          color: selected ? 'var(--text-primary)' : 'var(--text-muted)',
        }}>
        {selected ? selected.name : '— เลือกวัตถุดิบ —'}
      </button>

      {open && (
        <div style={{
          position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 60,
          background: 'var(--surface-2)', border: '1px solid var(--border)',
          borderRadius: 8, marginTop: 4, boxShadow: '0 8px 24px rgba(0,0,0,.12)',
          maxHeight: 260, overflowY: 'auto',
        }}>
          <input autoFocus value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder="พิมพ์ค้นหา หรือพิมพ์ชื่อใหม่"
            style={{
              width: '100%', fontSize: 12, border: 'none',
              borderBottom: '1px solid var(--border)', borderRadius: 0,
            }} />

          {matches.map((m) => (
            <div key={m.id} onClick={() => { onChange(m.id); setOpen(false); setQuery(''); }}
              style={{
                padding: '9px 11px', fontSize: 13, cursor: 'pointer',
                background: m.id === value ? 'var(--surface-1)' : 'transparent',
              }}>
              {m.name}
              <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 6 }}>
                {m.unit}
              </span>
            </div>
          ))}

          {matches.length === 0 && !canCreate && (
            <p style={{ fontSize: 12, color: 'var(--text-muted)', padding: '10px 11px', margin: 0 }}>
              ไม่พบวัตถุดิบ
            </p>
          )}

          {canCreate && (
            <div onClick={creating ? undefined : createMaterial}
              style={{
                padding: '10px 11px', fontSize: 13, cursor: 'pointer',
                borderTop: '1px solid var(--border)', color: 'var(--accent)',
                fontWeight: 500,
              }}>
              {creating ? 'กำลังสร้าง...' : `+ เพิ่ม "${query.trim()}" เป็นวัตถุดิบใหม่`}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
