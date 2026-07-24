import { useEffect, useState } from 'react';
import { useStore } from '../store/StoreContext';
import { api } from '../api/client';
import { useAuth } from '../auth/AuthContext';

const KIND_LABEL = {
  cooked: 'อาหารปรุงเอง',
  resale: 'ซื้อมาขายไป',
  service: 'ค่าบริการ (ไม่ตัดสต๊อก)',
};

export default function Recipes() {
  const { storeId } = useStore();
  const { can } = useAuth();
  const showMoney = can('view_money');
  const [items, setItems] = useState([]);
  const [materials, setMaterials] = useState([]);
  const [recipeMap, setRecipeMap] = useState({});
  const [drafts, setDrafts] = useState({});
  const [skips, setSkips] = useState([]);
  const [aiAvailable, setAiAvailable] = useState(false);
  const [editingItem, setEditingItem] = useState(null);
  const [rows, setRows] = useState([]);
  const [suggesting, setSuggesting] = useState(null);
  const [bulkRunning, setBulkRunning] = useState(false);
  const [error, setError] = useState('');

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
    api.suggestStatus(storeId).then((s) => setAiAvailable(s.available)).catch(() => setAiAvailable(false));
    refreshDrafts();
    api.listRecipeSkips(storeId).then(setSkips).catch(() => setSkips([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storeId]);

  function refreshDrafts() {
    api.listRecipeDrafts(storeId)
      .then((list) => setDrafts(Object.fromEntries(list.map((d) => [d.item_name, d]))))
      .catch(() => setDrafts({}));
  }

  if (!storeId) return <p>เลือกสาขาในหน้าตั้งค่าก่อน</p>;

  const materialUnit = (id) => materials.find((m) => m.id === id)?.unit || '';
  const materialCost = (id) => materials.find((m) => m.id === id)?.cost || 0;

  function recipeCost(recipe) {
    return recipe.reduce((sum, r) => sum + (Number(r.qty) || 0) * materialCost(r.material_id), 0);
  }

  const needsRecipe = items.filter(
    (it) => !(recipeMap[it.name] || []).length && !skips.includes(it.name));

  function openRecipe(item) {
    setRows((recipeMap[item.name] || []).map((r) => ({ ...r })));
    setEditingItem({ item, from: 'existing' });
  }

  function openFromDraft(item, draft) {
    // A draft carries names, not ids. Each line is pre-selected to the
    // material it matched; anything unmatched stays blank so it's obvious
    // which lines still need a decision.
    setRows(draft.ingredients.map((ing) => ({
      material_id: ing.match?.material_id || '',
      qty: ing.qty ?? '',
      suggested_name: ing.name,
      suggested_unit: ing.unit,
    })));
    setEditingItem({ item, from: 'ai', kind: draft.kind });
  }

  async function suggestOne(item) {
    setSuggesting(item.name);
    setError('');
    try {
      const draft = await api.suggestRecipe(storeId, item.name);
      if (draft.kind === 'service') {
        // Nothing to portion here. Offering the skip beats an empty form,
        // which would just look like the suggestion failed.
        if (window.confirm(
          `AI คิดว่า "${item.name}" เป็นค่าบริการ ไม่ต้องตัดสต๊อก\nทำเครื่องหมายว่าไม่ต้องมีสูตรเลยไหม?`)) {
          await api.skipRecipe(storeId, item.name);
          setSkips([...skips, item.name]);
          return;
        }
      }
      openFromDraft(item, draft);
    } catch (e) {
      setError(e.message);
    } finally {
      setSuggesting(null);
    }
  }

  async function suggestAll() {
    const names = needsRecipe.map((it) => it.name).slice(0, 60);
    if (!names.length) return;
    setBulkRunning(true);
    setError('');
    try {
      await api.suggestAllRecipes(storeId, names);
      refreshDrafts();
    } catch (e) {
      setError(e.message);
    } finally {
      setBulkRunning(false);
    }
  }

  async function markSkipped(item) {
    await api.skipRecipe(storeId, item.name);
    setSkips([...skips, item.name]);
    refreshDrafts();
  }

  async function unskip(name) {
    await api.unskipRecipe(storeId, name);
    setSkips(skips.filter((s) => s !== name));
  }

  function addRow() {
    setRows([...rows, { material_id: '', qty: '' }]);
  }
  function updateRow(idx, patch) {
    setRows(rows.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  }
  function removeRow(idx) {
    setRows(rows.filter((_, i) => i !== idx));
  }

  async function createMaterialForRow(idx) {
    const row = rows[idx];
    const name = window.prompt('ชื่อวัตถุดิบ', row.suggested_name || '');
    if (!name) return;
    const unit = window.prompt('หน่วยที่เก็บในคลัง (เช่น กรัม, ขวด, ฟอง)', row.suggested_unit || '');
    if (!unit) return;

    const id = `mat_${Date.now()}`;
    // No cost field on purpose: unit cost comes from what deliveries
    // actually charged, never from a number typed at recipe time.
    await api.upsertMaterial(storeId, id, { name: name.trim(), unit: unit.trim(), par_level: 0 });
    setMaterials(await api.getMaterials(storeId));
    updateRow(idx, { material_id: id });
  }

  const incompleteRows = rows.filter(
    (r) => !r.material_id || r.qty === '' || r.qty === null || Number(r.qty) <= 0).length;

  async function saveRecipe() {
    const payload = rows.map((r) => ({ material_id: r.material_id, qty: Number(r.qty) }));
    await api.setRecipe(storeId, editingItem.item.name, payload);
    setRecipeMap({ ...recipeMap, [editingItem.item.name]: payload });
    setEditingItem(null);
    refreshDrafts();
  }

  const draftCount = Object.keys(drafts).length;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
        <div>
          <p style={{ fontSize: 15, fontWeight: 500, margin: '0 0 4px' }}>สูตรอาหาร</p>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: 0 }}>
            ผูกสูตรอาหารกับเมนู ระบบจะตัดสต๊อกอัตโนมัติทุกครั้งที่ขาย และคำนวณต้นทุนต่อเมนูให้
          </p>
        </div>
        {aiAvailable && needsRecipe.length > 0 && (
          <button onClick={suggestAll} disabled={bulkRunning} style={{ whiteSpace: 'nowrap' }}>
            {bulkRunning ? 'กำลังร่าง...' : `🪄 ร่างสูตรที่ยังว่าง (${needsRecipe.length})`}
          </button>
        )}
      </div>

      {error && <p style={{ fontSize: 12, color: 'var(--text-danger)', marginBottom: 12 }}>{error}</p>}

      {draftCount > 0 && (
        <div style={{
          background: '#fdf3e3', border: '1px solid var(--text-warning)', borderRadius: 8,
          padding: '10px 12px', marginBottom: 12, fontSize: 12, color: 'var(--text-warning)',
        }}>
          มีร่างจาก AI {draftCount} เมนู รอกรอกปริมาณ — ยังไม่มีผลกับสต๊อกจนกว่าจะเปิดกรอกและบันทึกทีละเมนู
        </div>
      )}

      <div className="card">
        {items.map((it, idx) => {
          const recipe = recipeMap[it.name] || [];
          const draft = drafts[it.name];
          const skipped = skips.includes(it.name);
          const cost = recipeCost(recipe);
          return (
            <div key={it.id} style={{
              display: 'flex', alignItems: 'center', gap: 8, padding: '12px 0',
              borderBottom: idx < items.length - 1 ? '0.5px solid var(--border)' : 'none',
            }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <p style={{ fontSize: 14, margin: 0 }}>{it.name}</p>
                <p style={{
                  fontSize: 12, margin: '2px 0 0',
                  color: recipe.length ? 'var(--text-secondary)'
                    : skipped ? 'var(--text-muted)' : 'var(--text-warning)',
                }}>
                  {recipe.length
                    ? (showMoney ? `${recipe.length} วัตถุดิบ · ต้นทุน ฿${cost.toFixed(2)}/จาน` : `${recipe.length} วัตถุดิบ`)
                    : skipped ? 'ไม่ต้องมีสูตร'
                      : draft ? `ร่างจาก AI ${draft.ingredients.length} วัตถุดิบ · รอกรอกปริมาณ`
                        : 'ยังไม่ผูกสูตร'}
                </p>
              </div>

              {skipped ? (
                <button onClick={() => unskip(it.name)} style={{ fontSize: 11, padding: '4px 8px' }}>
                  เอากลับมา
                </button>
              ) : (
                <>
                  {draft ? (
                    <button onClick={() => openFromDraft(it, draft)}
                      style={{ background: 'var(--surface-1)', fontSize: 12 }}>
                      กรอกปริมาณ
                    </button>
                  ) : (
                    aiAvailable && !recipe.length && (
                      <button onClick={() => suggestOne(it)} disabled={suggesting === it.name}
                        title="ให้ AI ช่วยร่างว่าเมนูนี้ใช้วัตถุดิบอะไร"
                        style={{ fontSize: 12, padding: '4px 8px' }}>
                        {suggesting === it.name ? '...' : '🪄'}
                      </button>
                    )
                  )}
                  <button onClick={() => openRecipe(it)} style={{ fontSize: 12 }}>
                    {recipe.length ? 'แก้ไขสูตร' : 'ผูกเอง'}
                  </button>
                </>
              )}
            </div>
          );
        })}
      </div>

      {editingItem && (
        <div className="modal-overlay" onClick={() => setEditingItem(null)}>
          <div className="modal-box" style={{ width: 380, maxHeight: '85vh', overflowY: 'auto' }}
            onClick={(e) => e.stopPropagation()}>
            <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 4px' }}>
              {editingItem.from === 'ai' ? '🪄 ร่างสูตรจาก AI' : 'สูตรอาหาร'}
            </p>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '0 0 12px' }}>
              {editingItem.item.name}
              {editingItem.kind && (
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}> · {KIND_LABEL[editingItem.kind]}</span>
              )}
            </p>

            {editingItem.from === 'ai' && (
              <div style={{
                background: 'var(--surface-1)', borderRadius: 8, padding: '10px 12px',
                marginBottom: 12, fontSize: 11, color: 'var(--text-secondary)',
              }}>
                AI เสนอว่าน่าจะใช้วัตถุดิบเหล่านี้ — <b>ปริมาณต้องกรอกเอง</b> เพราะแต่ละร้านตักไม่เท่ากัน
                {editingItem.kind === 'resale' && ' (ขาย 1 = ตัด 1 ใส่ให้แล้ว ตรวจอีกรอบได้)'}
              </div>
            )}

            {rows.length === 0 && (
              <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                ยังไม่มีวัตถุดิบ กด "เพิ่มวัตถุดิบ" ด้านล่าง
              </p>
            )}

            {rows.map((r, idx) => (
              <div key={idx} style={{ marginBottom: 8 }}>
                {r.suggested_name && (
                  <p style={{ fontSize: 10, color: 'var(--text-muted)', margin: '0 0 2px' }}>
                    AI เสนอ: {r.suggested_name}{r.suggested_unit ? ` (${r.suggested_unit})` : ''}
                  </p>
                )}
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <select value={r.material_id} onChange={(e) => updateRow(idx, { material_id: e.target.value })}
                    style={{ flex: 1.3, fontSize: 12, minWidth: 0 }}>
                    <option value="">— เลือกวัตถุดิบ —</option>
                    {materials.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                  </select>
                  {!r.material_id && (
                    <button onClick={() => createMaterialForRow(idx)}
                      style={{ fontSize: 11, padding: '4px 6px', whiteSpace: 'nowrap' }}>
                      + สร้าง
                    </button>
                  )}
                  <input type="number" value={r.qty} placeholder="?"
                    onChange={(e) => updateRow(idx, { qty: e.target.value })}
                    style={{
                      width: 60, fontSize: 12,
                      borderColor: (r.qty === '' || Number(r.qty) <= 0) ? 'var(--text-warning)' : undefined,
                    }} />
                  <span style={{ fontSize: 12, color: 'var(--text-secondary)', minWidth: 30 }}>
                    {materialUnit(r.material_id)}
                  </span>
                  <button onClick={() => removeRow(idx)} style={{ fontSize: 11 }}>x</button>
                </div>
              </div>
            ))}

            <button onClick={addRow} style={{ width: '100%', marginTop: 8 }}>+ เพิ่มวัตถุดิบ</button>

            {showMoney && incompleteRows === 0 && rows.length > 0 && (
              <div style={{
                display: 'flex', justifyContent: 'space-between', fontSize: 13, fontWeight: 500,
                marginTop: 12, paddingTop: 12, borderTop: '0.5px solid var(--border)',
              }}>
                <span>ต้นทุนรวมต่อจาน</span>
                <span>฿{recipeCost(rows).toFixed(2)}</span>
              </div>
            )}

            {incompleteRows > 0 && (
              <p style={{ fontSize: 11, color: 'var(--text-warning)', margin: '12px 0 0' }}>
                ⚠ ยังกรอกไม่ครบ {incompleteRows} รายการ (ต้องเลือกวัตถุดิบและใส่ปริมาณมากกว่า 0)
              </p>
            )}

            <div style={{ display: 'flex', gap: 8, justifyContent: 'space-between', marginTop: 16 }}>
              {editingItem.from === 'ai' ? (
                <button onClick={() => { markSkipped(editingItem.item); setEditingItem(null); }}
                  style={{ fontSize: 11 }}>
                  ไม่ต้องมีสูตร
                </button>
              ) : <span />}
              <div style={{ display: 'flex', gap: 8 }}>
                <button onClick={() => setEditingItem(null)}>ยกเลิก</button>
                <button style={{ background: 'var(--surface-1)' }} onClick={saveRecipe}
                  disabled={rows.length === 0 || incompleteRows > 0}>
                  บันทึก
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
