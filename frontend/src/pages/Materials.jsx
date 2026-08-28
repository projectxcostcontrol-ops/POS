import { useEffect, useState } from 'react';
import { useStore } from '../store/StoreContext';
import { api } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { newId } from '../util/ids';
import SetupGate from '../components/SetupGate';

const UNITS = ['กรัม', 'กก.', 'มล.', 'ลิตร', 'ชิ้น', 'ขวด', 'ฟอง', 'ถุง', 'แพ็ค', 'กล่อง', 'ลัง', 'ตัว', 'คน', 'ชุด', 'กระสอบ'];
const CATEGORIES = [
  { value: 'ingredient', label: 'วัตถุดิบอาหาร' },
  { value: 'drink', label: 'เครื่องดื่ม' },
  { value: 'packaging', label: 'บรรจุภัณฑ์' },
  { value: 'consumable', label: 'ของใช้สิ้นเปลือง' },
];

function standardConversion(purchaseUnit, stockUnit) {
  const factors = {
    'กก.>กรัม': 1000,
    'กรัม>กก.': 0.001,
    'ลิตร>มล.': 1000,
    'มล.>ลิตร': 0.001,
  };
  if (purchaseUnit === stockUnit) return 1;
  return factors[`${purchaseUnit}>${stockUnit}`] ?? null;
}

export default function Materials() {
  const { storeId } = useStore();
  const { can } = useAuth();
  const showMoney = can('view_money');
  const [materials, setMaterials] = useState([]);
  const [items, setItems] = useState([]);
  const [recipeMap, setRecipeMap] = useState({});
  const [editing, setEditing] = useState(null);
  const [adjusting, setAdjusting] = useState(null);
  const [adjustReason, setAdjustReason] = useState('กรอกผิด');
  const [adjustVal, setAdjustVal] = useState('');
  const [historyFor, setHistoryFor] = useState(null);
  const [error, setError] = useState('');

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

  if (!storeId) return <SetupGate what="จัดการของในครัวได้" />;

  function status(m) {
    const stock = m.stock ?? 0;
    if (stock < 0) return { label: 'ติดลบ - ตรวจสอบ', color: 'var(--text-danger)' };
    if (stock === 0) return { label: 'หมด', color: 'var(--text-danger)' };
    if (!(Number(m.par) > 0)) return { label: 'ยังไม่ได้ตั้งจำนวนที่ควรมี', color: 'var(--text-warning)' };
    if (stock <= Number(m.par)) return { label: 'ต่ำกว่าที่ควรมี', color: 'var(--text-warning)' };
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
  const lowCount = materials.filter((m) => Number(m.par) > 0 && (m.stock ?? 0) <= Number(m.par)).length;
  const unsetParCount = materials.filter((m) => !(Number(m.par) > 0)).length;
  const qualityIssues = materials.flatMap((m) => {
    const issues = [];
    const costPerKg = m.unit === 'กรัม' ? Number(m.cost || 0) * 1000
      : m.unit === 'กก.' ? Number(m.cost || 0) : null;
    if (/ค่าแรง|เงินเดือน|ค่าจ้าง/.test(m.name || '')) issues.push(`${m.name}: ดูเหมือนเป็นค่าใช้จ่าย ไม่ใช่วัตถุดิบ`);
    if (costPerKg !== null && costPerKg > 5000) issues.push(`${m.name}: ต้นทุนเทียบเท่า ฿${costPerKg.toLocaleString()}/กก.`);
    return issues;
  });

  async function saveEdit(form) {
    try {
      setError('');
      const id = editing.id || newId();
      await api.upsertMaterial(storeId, id, {
        name: form.name, unit: form.unit, cost: parseFloat(form.cost) || 0,
        par: parseFloat(form.par) || 0, category: form.category,
        purchase_unit: form.purchase_unit || form.unit,
        purchase_to_stock: parseFloat(form.purchase_to_stock) || 1,
      });
      setEditing(null);
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  async function saveAdjust() {
    await api.adjustStock(storeId, adjusting.id, parseFloat(adjustVal) || 0, adjustReason);
    setAdjustReason('กรอกผิด');
    setAdjusting(null);
    load();
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <p style={{ fontSize: 15, fontWeight: 500, margin: 0 }}>ของในครัว</p>
        <button onClick={() => setEditing({})}>+ เพิ่มวัตถุดิบ</button>
      </div>
      {error && <p style={{ fontSize: 12, color: 'var(--text-danger)', margin: '0 0 12px' }}>{error}</p>}

      {negatives.length > 0 && (
        <div style={{
          background: '#fdeaea', border: '1px solid var(--text-danger)', borderRadius: 8,
          padding: '10px 14px', marginBottom: 16, fontSize: 13, color: 'var(--text-danger)',
        }}>
          ⚠ สต๊อกติดลบ: {negatives.map((m) => `${m.name} (${m.stock})`).join(', ')}
          <div style={{ fontSize: 12, marginTop: 4 }}>
            แปลว่าสูตรอาจใส่ปริมาณมากเกินจริง หรือยังไม่ได้บันทึกของที่ซื้อเข้ามา — ตรวจสอบแล้วเช็กสต๊อกใหม่
          </div>
        </div>
      )}

      {qualityIssues.length > 0 && (
        <div style={{
          background: '#fdf3e3', border: '1px solid var(--text-warning)', borderRadius: 8,
          padding: '10px 14px', marginBottom: 16, fontSize: 12, color: 'var(--text-warning)',
        }}>
          <b>พบข้อมูลที่ควรตรวจสอบ {qualityIssues.length} รายการ</b>
          {qualityIssues.slice(0, 3).map((issue) => <div key={issue} style={{ marginTop: 4 }}>• {issue}</div>)}
          {qualityIssues.length > 3 && <div style={{ marginTop: 4 }}>และอีก {qualityIssues.length - 3} รายการ</div>}
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
        <div className="stat-card">
          <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '0 0 6px' }}>ยังไม่ตั้งระดับที่ควรมี</p>
          <p style={{ fontSize: 24, fontWeight: 500, margin: 0, color: unsetParCount ? 'var(--text-warning)' : undefined }}>
            {unsetParCount}
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
                style={{ fontSize: 12, padding: '6px 8px' }}>แก้ไขจำนวน</button>
              <button onClick={() => setEditing(m)} style={{ fontSize: 12, padding: '6px 8px' }}>แก้ไข</button>
            </div>
          );
        })}
      </div>

      {editing && <EditModal material={editing} onCancel={() => setEditing(null)} onSave={saveEdit} />}

      {adjusting && (
        <div className="modal-overlay" onClick={() => setAdjusting(null)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 4px' }}>แก้ไขจำนวน</p>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '0 0 16px' }}>{adjusting.name}</p>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              แก้เป็น ({adjusting.unit})
            </label>
            <input type="number" value={adjustVal} onChange={(e) => setAdjustVal(e.target.value)}
              style={{ width: '100%', margin: '4px 0 12px' }} />

            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>เหตุผล</label>
            <select value={adjustReason} onChange={(e) => setAdjustReason(e.target.value)}
              style={{ width: '100%', margin: '4px 0 12px' }}>
              <option value="กรอกผิด">กรอกผิด</option>
              <option value="รับของแล้วลืมบันทึก">รับของแล้วลืมบันทึก</option>
              <option value="อื่น ๆ">อื่น ๆ</option>
            </select>

            <div style={{
              background: 'var(--surface-1)', borderRadius: 8, padding: '10px 12px',
              marginBottom: 16, fontSize: 11, color: 'var(--text-secondary)',
            }}>
              การแก้ตรงนี้จะ<b>ไม่ถูกนับเป็นส่วนต่าง</b>ในรายงาน และจะกลบส่วนต่างที่สะสมอยู่ด้วย
              <br />
              ถ้ากำลังตรวจนับตามรอบ ให้ใช้หน้า{' '}
              <a href="/stock-count" style={{ color: 'var(--accent)' }}>เช็กสต๊อกวัตถุดิบ</a> แทน
            </div>
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
  receive: 'ซื้อเข้า', sale: 'ขาย', count: 'เช็กสต๊อก', waste: 'ของเสีย',
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
    category: material.category || 'ingredient',
    purchase_unit: material.purchase_unit || material.unit || UNITS[0],
    purchase_to_stock: material.purchase_to_stock ?? 1,
  });
  const cost = Number(form.cost) || 0;
  const conversion = Number(form.purchase_to_stock) || 0;
  const unitsDiffer = form.purchase_unit !== form.unit;
  const standardFactor = standardConversion(form.purchase_unit, form.unit);
  const conversionUnconfirmed = unitsDiffer && conversion === 1 && standardFactor === null;
  const costPerKg = form.unit === 'กรัม' ? cost * 1000 : form.unit === 'กก.' ? cost : null;
  const warnings = [];
  if (!form.name.trim()) warnings.push('กรุณาระบุชื่อวัตถุดิบ');
  if (conversion <= 0) warnings.push('อัตราแปลงต้องมากกว่า 0');
  if (conversionUnconfirmed) warnings.push(`หน่วยซื้อ “${form.purchase_unit}” ไม่ตรงกับหน่วยสต๊อก “${form.unit}” กรุณาระบุอัตราแปลงที่ถูกต้อง`);
  if (cost < 0 || Number(form.par) < 0) warnings.push('ต้นทุนและจำนวนที่ควรมีต้องไม่ติดลบ');
  if (costPerKg !== null && costPerKg > 5000) warnings.push(`ต้นทุนเทียบเท่า ฿${costPerKg.toLocaleString()}/กก. สูงผิดปกติ กรุณาตรวจหน่วยอีกครั้ง`);
  if (form.unit === 'กรัม' && cost >= 10) warnings.push('ราคาต่อกรัมสูงผิดปกติ คุณอาจต้องการเลือกหน่วย “กก.”');
  const canSave = warnings.length === 0;
  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 16px' }}>
          {material.id ? 'แก้ไขวัตถุดิบ' : 'เพิ่มวัตถุดิบ'}
        </p>
        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>ชื่อวัตถุดิบ</label>
        <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
          style={{ width: '100%', margin: '4px 0 12px' }} />
        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>ประเภท</label>
        <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}
          style={{ width: '100%', margin: '4px 0 12px' }}>
          {CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
        </select>
        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>หน่วยวัด</label>
        <select value={form.unit} onChange={(e) => {
          const unit = e.target.value;
          const suggested = standardConversion(form.purchase_unit, unit);
          setForm({ ...form, unit, purchase_to_stock: suggested ?? form.purchase_to_stock });
        }}
          style={{ width: '100%', margin: '4px 0 12px' }}>
          {UNITS.map((u) => <option key={u}>{u}</option>)}
        </select>
        <div style={{ background: 'var(--surface-1)', borderRadius: 8, padding: 10, marginBottom: 12 }}>
          <p style={{ fontSize: 11.5, fontWeight: 600, margin: '0 0 8px' }}>การแปลงหน่วยตอนซื้อ</p>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 72px 1fr', gap: 6, alignItems: 'center' }}>
            <select value={form.purchase_unit} onChange={(e) => {
              const purchase_unit = e.target.value;
              const suggested = standardConversion(purchase_unit, form.unit);
              setForm({ ...form, purchase_unit, purchase_to_stock: suggested ?? 1 });
            }}
              style={{ minWidth: 0, fontSize: 12 }}>
              {UNITS.map((u) => <option key={u}>{u}</option>)}
            </select>
            <input type="number" min="0" step="any" value={form.purchase_to_stock}
              onChange={(e) => setForm({ ...form, purchase_to_stock: e.target.value })}
              style={{ minWidth: 0, fontSize: 12 }} />
            <span style={{ fontSize: 11.5, color: 'var(--text-secondary)' }}>{form.unit}</span>
          </div>
          <p style={{ fontSize: 10.5, color: 'var(--text-muted)', margin: '6px 0 0' }}>
            1 {form.purchase_unit} = {form.purchase_to_stock || '?'} {form.unit} เช่น 1 ถุง = 5,000 กรัม
          </p>
          <div style={{
            marginTop: 8, padding: '8px 9px', borderRadius: 7, fontSize: 11.5,
            background: conversionUnconfirmed ? '#fdf3e3' : unitsDiffer ? '#edf6ed' : 'var(--surface-2)',
            color: conversionUnconfirmed ? 'var(--text-warning)'
              : unitsDiffer ? 'var(--text-success)' : 'var(--text-secondary)',
          }}>
            {conversionUnconfirmed
              ? `⚠ หน่วยไม่ตรงกัน — ระบุว่า 1 ${form.purchase_unit} มีกี่ ${form.unit}`
              : unitsDiffer
                ? `✓ ระบบจะแปลง 1 ${form.purchase_unit} เป็น ${conversion.toLocaleString()} ${form.unit} อัตโนมัติเมื่อรับของ`
                : `หน่วยซื้อและหน่วยสต๊อกตรงกัน ไม่ต้องแปลงหน่วย`}
            {unitsDiffer && !conversionUnconfirmed && cost > 0 && (
              <div style={{ marginTop: 3 }}>
                ต้นทุนเทียบเท่าต่อ 1 {form.purchase_unit}: ฿{(cost * conversion).toLocaleString(undefined, { maximumFractionDigits: 2 })}
              </div>
            )}
          </div>
        </div>
        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>ต้นทุนตั้งต้น (บาท/หน่วย)</label>
        <input type="number" value={form.cost} onChange={(e) => setForm({ ...form, cost: e.target.value })}
          style={{ width: '100%', margin: '4px 0 4px' }} />
        <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 12px' }}>
          ใช้จนกว่าจะซื้อของเข้าครั้งแรก จากนั้นระบบจะใช้ต้นทุนเฉลี่ยจริงแทน
        </p>
        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>จำนวนที่ควรมีสต๊อก (par)</label>
        <input type="number" value={form.par} onChange={(e) => setForm({ ...form, par: e.target.value })}
          style={{ width: '100%', margin: '4px 0 16px' }} />
        {warnings.length > 0 && (
          <div style={{ background: '#fdf3e3', border: '1px solid var(--text-warning)', borderRadius: 8,
            padding: '9px 11px', marginBottom: 12, fontSize: 11.5, color: 'var(--text-warning)' }}>
            {warnings.map((w) => <div key={w}>• {w}</div>)}
          </div>
        )}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onCancel}>ยกเลิก</button>
          <button style={{ background: 'var(--surface-1)' }} onClick={() => onSave(form)} disabled={!canSave}>บันทึก</button>
        </div>
      </div>
    </div>
  );
}
