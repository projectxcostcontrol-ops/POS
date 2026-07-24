import { useEffect, useState } from 'react';
import { useStore } from '../store/StoreContext';
import { api } from '../api/client';
import { useAuth } from '../auth/AuthContext';

export default function Recipes() {
  const { storeId } = useStore();
  const { can } = useAuth();
  const showMoney = can('view_money');
  const [items, setItems] = useState([]);
  const [materials, setMaterials] = useState([]);
  const [recipeMap, setRecipeMap] = useState({}); // { itemName: [{material_id, qty}] }
  const [editingItem, setEditingItem] = useState(null);
  const [rows, setRows] = useState([]);

  useEffect(() => {
    if (!storeId) return;
    Promise.all([api.getItems(storeId), api.getMaterials(storeId)]).then(async ([itemList, mats]) => {
      setItems(itemList);
      setMaterials(mats);
      const pairs = await Promise.all(
        itemList.map((it) => api.getRecipe(storeId, it.name).then((r) => [it.name, r]))
      );
      setRecipeMap(Object.fromEntries(pairs));
    });
  }, [storeId]);

  if (!storeId) return <p>เลือกสาขาในหน้าตั้งค่าก่อน</p>;

  const materialUnit = (id) => materials.find((m) => m.id === id)?.unit || '';
  const materialCost = (id) => materials.find((m) => m.id === id)?.cost || 0;

  function recipeCost(recipe) {
    return recipe.reduce((sum, r) => sum + r.qty * materialCost(r.material_id), 0);
  }

  async function openRecipe(item) {
    const recipe = recipeMap[item.name] || [];
    setRows(recipe.map((r) => ({ ...r })));
    setEditingItem(item);
  }
  function addRow() {
    if (materials.length === 0) return;
    setRows([...rows, { material_id: materials[0].id, qty: 0 }]);
  }
  function updateRow(idx, patch) {
    setRows(rows.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  }
  function removeRow(idx) {
    setRows(rows.filter((_, i) => i !== idx));
  }
  async function saveRecipe() {
    await api.setRecipe(storeId, editingItem.name, rows);
    setRecipeMap({ ...recipeMap, [editingItem.name]: rows });
    setEditingItem(null);
  }

  return (
    <div>
      <p style={{ fontSize: 15, fontWeight: 500, margin: '0 0 4px' }}>สูตรอาหาร</p>
      <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '0 0 16px' }}>
        ผูกสูตรอาหารกับเมนู ระบบจะตัดสต๊อกอัตโนมัติทุกครั้งที่ขาย และคำนวณต้นทุนต่อเมนูให้
      </p>

      <div className="card">
        {items.map((it, idx) => {
          const recipe = recipeMap[it.name] || [];
          const cost = recipeCost(recipe);
          return (
            <div key={it.id} style={{
              display: 'flex', alignItems: 'center', gap: 12, padding: '12px 0',
              borderBottom: idx < items.length - 1 ? '0.5px solid var(--border)' : 'none',
            }}>
              <div style={{ flex: 1 }}>
                <p style={{ fontSize: 14, margin: 0 }}>{it.name}</p>
                <p style={{ fontSize: 12, margin: '2px 0 0', color: recipe.length ? 'var(--text-secondary)' : 'var(--text-warning)' }}>
                  {recipe.length
                    ? (showMoney ? `${recipe.length} วัตถุดิบ · ต้นทุน ฿${cost.toFixed(2)}/จาน` : `${recipe.length} วัตถุดิบ`)
                    : 'ยังไม่ผูกสูตร'}
                </p>
              </div>
              <button onClick={() => openRecipe(it)}>{recipe.length ? 'แก้ไขสูตร' : 'ผูกสูตร'}</button>
            </div>
          );
        })}
      </div>

      {editingItem && (
        <div className="modal-overlay" onClick={() => setEditingItem(null)}>
          <div className="modal-box" style={{ width: 320 }} onClick={(e) => e.stopPropagation()}>
            <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 4px' }}>สูตรอาหาร</p>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '0 0 16px' }}>{editingItem.name}</p>
            {rows.length === 0 && (
              <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>ยังไม่มีวัตถุดิบ กด "เพิ่มวัตถุดิบ" ด้านล่าง</p>
            )}
            {rows.map((r, idx) => (
              <div key={idx} style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 8 }}>
                <select value={r.material_id} onChange={(e) => updateRow(idx, { material_id: e.target.value })}
                  style={{ flex: 1.3, fontSize: 12 }}>
                  {materials.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                </select>
                <input type="number" value={r.qty} onChange={(e) => updateRow(idx, { qty: parseFloat(e.target.value) || 0 })}
                  style={{ width: 64, fontSize: 12 }} />
                <span style={{ fontSize: 12, color: 'var(--text-secondary)', minWidth: 32 }}>
                  {materialUnit(r.material_id)}
                </span>
                <button onClick={() => removeRow(idx)}>x</button>
              </div>
            ))}
            <button onClick={addRow} style={{ width: '100%', marginTop: 8 }}>+ เพิ่มวัตถุดิบ</button>
            {showMoney && (
              <div style={{
                display: 'flex', justifyContent: 'space-between', fontSize: 13, fontWeight: 500,
                marginTop: 12, paddingTop: 12, borderTop: '0.5px solid var(--border)',
              }}>
                <span>ต้นทุนรวมต่อจาน</span>
                <span>฿{recipeCost(rows).toFixed(2)}</span>
              </div>
            )}
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
              <button onClick={() => setEditingItem(null)}>ยกเลิก</button>
              <button style={{ background: 'var(--surface-1)' }} onClick={saveRecipe}>บันทึก</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
