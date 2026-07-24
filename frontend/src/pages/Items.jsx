import { useEffect, useState } from 'react';
import { useStore } from '../store/StoreContext';
import { api } from '../api/client';

export default function Items() {
  const { storeId } = useStore();
  const [items, setItems] = useState([]);
  const [categories, setCategories] = useState([]);
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [showCatManager, setShowCatManager] = useState(false);
  const [newCatName, setNewCatName] = useState('');
  const [editingCatId, setEditingCatId] = useState(null);
  const [editingCatName, setEditingCatName] = useState('');

  function load() {
    if (!storeId) return;
    api.getItems(storeId).then(setItems);
    api.getCategories(storeId).then(setCategories);
  }
  useEffect(load, [storeId]);

  if (!storeId) return <p>เลือกสาขาในหน้าตั้งค่าก่อน</p>;

  const catName = (id) => categories.find((c) => c.id === id)?.name || 'ไม่มีหมวดหมู่';
  const visible = items
    .filter((i) => filter === 'all' || i.category_id === filter)
    .filter((i) => i.name.toLowerCase().includes(search.toLowerCase()));

  async function addCategory() {
    if (!newCatName.trim()) return;
    await api.createCategory(storeId, newCatName.trim());
    setNewCatName('');
    load();
  }
  async function saveRename(catId) {
    if (editingCatName.trim()) await api.renameCategory(storeId, catId, editingCatName.trim());
    setEditingCatId(null);
    load();
  }
  async function removeCategory(catId) {
    await api.deleteCategory(storeId, catId);
    load();
  }
  async function assignCategory(itemName, categoryId) {
    await api.setItemCategory(storeId, itemName, categoryId);
    load();
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <p style={{ fontSize: 15, fontWeight: 500, margin: 0 }}>รายการสินค้า</p>
        <button onClick={() => window.open('https://r.loyverse.com/dashboard/#/items/inventory', '_blank')}>
          + เพิ่มเมนู (เปิด Loyverse)
        </button>
      </div>
      <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '-12px 0 16px' }}>
        เพิ่ม/แก้ไขเมนูทำผ่าน Loyverse โดยตรง เพิ่มเสร็จแล้วกลับมาหน้านี้แล้วรีเฟรชเพื่อดึงเมนูใหม่มาโชว์
      </p>

      <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
        <input placeholder="ค้นหาเมนู" value={search} onChange={(e) => setSearch(e.target.value)}
          style={{ flex: 1, minWidth: 160 }} />
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="all">ทุกหมวดหมู่</option>
          {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <button onClick={() => setShowCatManager(true)}>จัดการหมวดหมู่</button>
      </div>

      <div className="card">
        {visible.length === 0 && <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>ไม่พบเมนู</p>}
        {visible.map((it, idx) => (
          <div key={it.id} style={{
            display: 'flex', alignItems: 'center', gap: 12, padding: '12px 0',
            borderBottom: idx < visible.length - 1 ? '0.5px solid var(--border)' : 'none',
          }}>
            <div style={{ flex: 1 }}>
              <p style={{ fontSize: 14, margin: 0 }}>{it.name}</p>
            </div>
            <select value={it.category_id || ''} onChange={(e) => assignCategory(it.name, e.target.value)}
              style={{ fontSize: 12 }}>
              <option value="">ไม่มีหมวดหมู่</option>
              {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <p style={{ fontSize: 14, margin: 0 }}>{it.price != null ? `฿${it.price}` : '-'}</p>
          </div>
        ))}
      </div>

      {showCatManager && (
        <div className="modal-overlay" onClick={() => setShowCatManager(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 12px' }}>จัดการหมวดหมู่</p>
            {categories.map((c) => (
              <div key={c.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0' }}>
                {editingCatId === c.id ? (
                  <>
                    <input value={editingCatName} onChange={(e) => setEditingCatName(e.target.value)}
                      style={{ flex: 1, fontSize: 13 }} />
                    <button onClick={() => saveRename(c.id)}>✓</button>
                  </>
                ) : (
                  <>
                    <span style={{ flex: 1, fontSize: 13 }}>{c.name}</span>
                    <button onClick={() => { setEditingCatId(c.id); setEditingCatName(c.name); }}>แก้ไข</button>
                    <button onClick={() => removeCategory(c.id)}>ลบ</button>
                  </>
                )}
              </div>
            ))}
            <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
              <input placeholder="ชื่อหมวดหมู่ใหม่" value={newCatName}
                onChange={(e) => setNewCatName(e.target.value)} style={{ flex: 1 }} />
              <button onClick={addCategory}>เพิ่ม</button>
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
              <button style={{ background: 'var(--surface-1)' }} onClick={() => setShowCatManager(false)}>เสร็จสิ้น</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
