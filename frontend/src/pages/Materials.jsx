import { useEffect, useState } from 'react';
import { useStore } from '../store/StoreContext';
import { api } from '../api/client';
import { useAuth } from '../auth/AuthContext';

const UNITS = ['กรัม', 'กก.', 'มล.', 'ลิตร', 'ชิ้น', 'ขวด'];

export default function Materials() {
  const { storeId } = useStore();
  const { can } = useAuth();
  const showMoney = can('view_money');
  const [materials, setMaterials] = useState([]);
  const [items, setItems] = useState([]);
  const [recipeMap, setRecipeMap] = useState({});
  const [editing, setEditing] = useState(null);
  const [adjusting, setAdjusting] = useState(null);
  const [adjustVal, setAdjustVal] = useState('');
  const [historyFor, setHistoryFor] = useState(null);

  function load() {
    if (!storeId) return;
    api.getMaterials(storeId).then(setMaterials);
    api.getItems(storeId).then(async (itemList) => {
      setItems(itemList);
      const pairs = await Promise.all(
        itemList.map((it) => api.getRecipe(storeId, it.name).then((r) => [it.name, r]))
      );
      setRecipeMap(Object.fromEntries(pairs));
    });
  }
  useEffect(load, [storeId]);

  if (!storeId) return <p>เลือกสาขาในหน้าตั้งค่าก่อน</p>;

  function status(m) {
    const stock = m.stock ?? 0;
    if (stock < 0) return { label: 'ติดลบ - ตรวจสอบ', color: 'var(--text-danger)' };
    if (stock === 0) return { label: 'หมด', color: 'var(--text-danger)' };
    if (stock <= (m.par || 0)) return { label: 'ต่ำกว่าที่ควรมี', color: 'var(--text-warning)' };
    return { label: 'ปกติ', color: 'var(--text-muted)' };
  }

  function sellableCount(itemName) {
    const recipe = recipeMap[itemName];
    if (!recipe || recipe.length === 0) return null;
    const counts = recipe.map((ing) => {
      const mat = materials.find((m) => m.id === ing.material_id);
      if (!mat || !ing.qty) return 0;
      return Math.floor(Math.max(0, mat.stock ?? 0) / ing.qty);
    });
    return Math.min(...counts);
  }

  const negatives = materials.filter((m) => (m.stock ?? 0) < 0);
  const totalValue = materials.reduce((s, m) => s + Math.max(0, m.stock ?? 0) * (m.cost || 0), 0);
  const lowCount = materials.filter((m) => (m.stock ?? 0) <= (m.par || 0)).length;

  async function saveEdit(form) {
    const id = editing.id || form.name.trim().toLowerCase().replace(/\s+/g, '-');
    await api.upsertMaterial(storeId, id, {
      name: form.name, unit: form.unit, cost: parseFloat(form.cost) || 0,
      par: parseFloat(form.par) || 0,
    });
    setEditing(null);
    load();
  }

  async function saveAdjust() {
    await api.adjustStock(storeId, adjusting.id, parseFloat(adjustVal) || 0);
    setAdjusting(null);
    load();
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <p style={{ fontSize: 15, fontWeight: 500, margin: 0 }}>วัตถุดิบและสต๊อก</p>
        <button onClick={() => setEditing({})}>+ เพิ่มวัตถุดิบ</button>
      </div>

      {negatives.length > 0 && (
        <div style={{
          background: '#fdeaea', border: '1px solid var(--text-danger)', borderRadius: 8,
          padding: '10px 14px', marginBottom: 16, fontSize: 13, color: 'var(--text-danger)',
        }}>
          ⚠ สต๊อกติดลบ: {negatives.map((m) => `${m.name} (${m.stock})`).join(', ')}
          <div style={{ fontSize: 12, marginTop: 4 }}>
            แปลว่าสูตรอาจใส่ปริมาณมากเกินจริง หรือยังไม่ได้บันทึกของที่รับเข้ามา - ตรวจสอบแล้วนับสต๊อกใหม่
          </div>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, marginBottom: 24 }}>
        {showMoney && (
          <div className="stat-card">
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '0 0 6px' }}>มูลค่าสต๊อกรวม</p>
            <p style={{ fontSize: 24, fontWeight: 500, margin: 0 }}>
              ฿{Math.round(totalValue).toLocaleString()}
            </p>
          </div>
        )}
        <div className="stat-card">
          <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '0 0 6px' }}>ต่ำกว่าที่ควรมี</p>
          <p style={{ fontSize: 24, fontWeight: 500, margin: 0, color: lowCount ? 'var(--text-warning)' : undefined }}>
            {lowCount}
          </p>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 4px' }}>เมนูที่ยังทำขายได้</p>
        <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 12px' }}>
          คำนวณจากสต๊อกคงเหลือเทียบกับสูตรอาหารของแต่ละเมนู
        </p>
        {items.map((it, idx) => {
          const count = sellableCount(it.name);
          return (
            <div key={it.id} style={{
              display: 'flex', justifyContent: 'space-between', padding: '8px 0', fontSize: 13,
              borderBottom: idx < items.length - 1 ? '0.5px solid var(--border)' : 'none',
            }}>
              <span>{it.name}</span>
              {count === null ? (
                <span style={{ color: 'var(--text-muted)' }}>ยังไม่ผูกสูตร</span>
              ) : (
                <span style={{ color: count === 0 ? 'var(--text-danger)' : count <= 5 ? 'var(--text-warning)' : 'var(--text-secondary)' }}>
                  ทำได้อีก {count} จาน
                </span>
              )}
            </div>
          );
        })}
      </div>

      <div className="card">
        {materials.length === 0 && <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>ยังไม่มีวัตถุดิบ</p>}
        {materials.map((m, idx) => {
          const s = status(m);
          return (
            <div key={m.id} style={{
              display: 'flex', alignItems: 'center', gap: 8, padding: '12px 0',
              borderBottom: idx < materials.length - 1 ? '0.5px solid var(--border)' : 'none',
            }}>
              <div style={{ flex: 1 }}>
                <p style={{ fontSize: 14, margin: 0 }}>{m.name}</p>
                <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '2px 0 0' }}>
                  ควรมี {m.par ?? 0} {m.unit}
                  {showMoney && ` · ต้นทุนเฉลี่ย ฿${(m.cost || 0).toFixed(2)}/${m.unit}`}
                </p>
              </div>
              <div style={{ textAlign: 'right' }}>
                <p style={{ fontSize: 14, margin: 0, color: (m.stock ?? 0) < 0 ? 'var(--text-danger)' : undefined }}>
                  {(m.stock ?? 0).toLocaleString()} {m.unit}
                </p>
                <p style={{ fontSize: 12, margin: '2px 0 0', color: s.color }}>{s.label}</p>
              </div>
              <button onClick={() => setHistoryFor(m)} style={{ fontSize: 12, padding: '6px 8px' }}>ประวัติ</button>
              <button onClick={() => { setAdjusting(m); setAdjustVal(String(m.stock ?? 0)); }}
                style={{ fontSize: 12, padding: '6px 8px' }}>นับสต๊อก</button>
              <button onClick={() => setEditing(m)} style={{ fontSize: 12, padding: '6px 8px' }}>แก้ไข</button>
            </div>
          );
        })}
      </div>

      {editing && <EditModal material={editing} onCancel={() => setEditing(null)} onSave={saveEdit} />}

      {adjusting && (
        <div className="modal-overlay" onClick={() => setAdjusting(null)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 4px' }}>นับสต๊อกจริง</p>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '0 0 16px' }}>{adjusting.name}</p>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              จำนวนที่นับได้ ({adjusting.unit})
            </label>
            <input type="number" value={adjustVal} onChange={(e) => setAdjustVal(e.target.value)}
              style={{ width: '100%', margin: '4px 0 8px' }} />
            <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 16px' }}>
              ระบบจะบันทึกส่วนต่างไว้เป็นประวัติ ไม่ได้ลบข้อมูลเดิมทิ้ง
            </p>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button onClick={() => setAdjusting(null)}>ยกเลิก</button>
              <button style={{ background: 'var(--surface-1)' }} onClick={saveAdjust}>บันทึก</button>
            </div>
          </div>
        </div>
      )}

      {historyFor && (
        <HistoryModal storeId={storeId} material={historyFor} onClose={() => setHistoryFor(null)} />
      )}
    </div>
  );
}

const KIND_LABEL = {
  receive: 'รับเข้า', sale: 'ขาย', count: 'นับสต๊อก', waste: 'ของเสีย',
};

function HistoryModal({ storeId, material, onClose }) {
  const [movements, setMovements] = useState(null);

  useEffect(() => {
    api.getMovements(storeId, material.id).then(setMovements);
  }, [storeId, material.id]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" style={{ width: 380, maxHeight: '70vh', overflowY: 'auto' }}
        onClick={(e) => e.stopPropagation()}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 4px' }}>ประวัติการเคลื่อนไหว</p>
        <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '0 0 16px' }}>{material.name}</p>

        {movements === null && <p style={{ fontSize: 13 }}>กำลังโหลด...</p>}
        {movements && movements.length === 0 && (
          <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>ยังไม่มีการเคลื่อนไหว</p>
        )}
        {movements && movements.map((mv, idx) => (
          <div key={mv.id || idx} style={{
            display: 'flex', justifyContent: 'space-between', padding: '8px 0', fontSize: 12,
            borderBottom: idx < movements.length - 1 ? '0.5px solid var(--border)' : 'none',
          }}>
            <span style={{ flex: 1 }}>
              {KIND_LABEL[mv.kind] || mv.kind}
              {mv.note && <span style={{ color: 'var(--text-muted)' }}> · {mv.note}</span>}
            </span>
            <span style={{ color: 'var(--text-muted)', marginRight: 8 }}>
              {(mv.occurred_at || '').slice(0, 10)}
            </span>
            <span style={{ color: mv.quantity >= 0 ? 'var(--text-success)' : 'var(--text-danger)' }}>
              {mv.quantity >= 0 ? '+' : ''}{mv.quantity} {material.unit}
            </span>
          </div>
        ))}

        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
          <button style={{ background: 'var(--surface-1)' }} onClick={onClose}>ปิด</button>
        </div>
      </div>
    </div>
  );
}

function EditModal({ material, onCancel, onSave }) {
  const [form, setForm] = useState({
    name: material.name || '', unit: material.unit || UNITS[0],
    cost: material.cost ?? 0, par: material.par ?? 0,
  });
  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 16px' }}>
          {material.id ? 'แก้ไขวัตถุดิบ' : 'เพิ่มวัตถุดิบ'}
        </p>
        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>ชื่อวัตถุดิบ</label>
        <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
          style={{ width: '100%', margin: '4px 0 12px' }} />
        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>หน่วยวัด</label>
        <select value={form.unit} onChange={(e) => setForm({ ...form, unit: e.target.value })}
          style={{ width: '100%', margin: '4px 0 12px' }}>
          {UNITS.map((u) => <option key={u}>{u}</option>)}
        </select>
        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>ต้นทุนตั้งต้น (บาท/หน่วย)</label>
        <input type="number" value={form.cost} onChange={(e) => setForm({ ...form, cost: e.target.value })}
          style={{ width: '100%', margin: '4px 0 4px' }} />
        <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 12px' }}>
          ใช้จนกว่าจะมีการรับของเข้าครั้งแรก จากนั้นระบบจะใช้ต้นทุนเฉลี่ยจริงแทน
        </p>
        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>จำนวนที่ควรมีสต๊อก (par)</label>
        <input type="number" value={form.par} onChange={(e) => setForm({ ...form, par: e.target.value })}
          style={{ width: '100%', margin: '4px 0 16px' }} />
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onCancel}>ยกเลิก</button>
          <button style={{ background: 'var(--surface-1)' }} onClick={() => onSave(form)}>บันทึก</button>
        </div>
      </div>
    </div>
  );
}
