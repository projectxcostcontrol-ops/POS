import { useEffect, useState } from 'react';
import { useStore } from '../store/StoreContext';
import { api } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import MaterialPicker from '../components/MaterialPicker';
import SetupGate from '../components/SetupGate';

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
  const [error, setError] = useState('');
  const [salesItems, setSalesItems] = useState([]);

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
    const now = new Date();
    const start = new Date(now.getFullYear(), now.getMonth(), 1);
    api.getSalesOverview(storeId, start.toISOString(), now.toISOString(), 'day', 0, false)
      .then((s) => setSalesItems(s.top_items || [])).catch(() => setSalesItems([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storeId]);

  function refreshDrafts() {
    api.listRecipeDrafts(storeId)
      .then((list) => setDrafts(Object.fromEntries(list.map((d) => [d.item_name, d]))))
      .catch(() => setDrafts({}));
  }

  if (!storeId) return <SetupGate what="ผูกสูตรอาหารได้" />;

  const materialUnit = (id) => materials.find((m) => m.id === id)?.unit || '';
  const materialCost = (id) => materials.find((m) => m.id === id)?.cost || 0;

  function recipeCost(recipe) {
    return recipe.reduce((sum, r) => sum + (Number(r.qty) || 0) * materialCost(r.material_id), 0);
  }

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
          `ระบบคิดว่า "${item.name}" เป็นค่าบริการ ไม่ต้องตัดสต๊อก\nทำเครื่องหมายว่าไม่ต้องมีสูตรเลยไหม?`)) {
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

  const incompleteRows = rows.filter(
    (r) => !r.material_id || r.qty === '' || r.qty === null || Number(r.qty) <= 0).length;
  const selectedIds = rows.map((r) => r.material_id).filter(Boolean);
  const duplicateRows = selectedIds.length - new Set(selectedIds).size;

  async function saveRecipe() {
    const payload = rows.map((r) => ({ material_id: r.material_id, qty: Number(r.qty) }));
    await api.setRecipe(storeId, editingItem.item.name, payload);
    setRecipeMap({ ...recipeMap, [editingItem.item.name]: payload });
    setEditingItem(null);
    refreshDrafts();
  }

  const draftCount = Object.keys(drafts).length;
  const coveredNames = new Set([
    ...Object.entries(recipeMap).filter(([, recipe]) => recipe?.length).map(([name]) => name),
    ...skips,
  ]);
  const totalRevenue = salesItems.reduce((sum, row) => sum + (Number(row.revenue) || 0), 0);
  const coveredRevenue = salesItems.reduce((sum, row) =>
    sum + (coveredNames.has(row.name) ? (Number(row.revenue) || 0) : 0), 0);
  const coveragePct = totalRevenue > 0 ? Math.round((coveredRevenue / totalRevenue) * 100) : 0;
  const completedCount = items.filter((it) => coveredNames.has(it.name)).length;
  const priorityMissing = salesItems.filter((row) => !coveredNames.has(row.name)).slice(0, 3);

  return (
    <div>
      <p style={{ fontSize: 15, fontWeight: 500, margin: '0 0 4px' }}>สูตรอาหาร</p>
      <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '0 0 16px' }}>
        ผูกสูตรอาหารกับเมนู ระบบจะตัดสต๊อกอัตโนมัติทุกครั้งที่ขาย และคำนวณต้นทุนต่อเมนูให้
      </p>

      {error && <p style={{ fontSize: 12, color: 'var(--text-danger)', marginBottom: 12 }}>{error}</p>}

      <div className="card" style={{ marginBottom: 12 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'baseline' }}>
          <div>
            <p style={{ fontSize: 13.5, fontWeight: 600, margin: 0 }}>ความครบของสูตรเดือนนี้</p>
            <p style={{ fontSize: 11.5, color: 'var(--text-muted)', margin: '3px 0 0' }}>
              ผูกแล้ว {completedCount} จาก {items.length} เมนู · วัดตามยอดขายที่คำนวณต้นทุนได้
            </p>
          </div>
          <span style={{ fontSize: 22, fontWeight: 700,
            color: coveragePct >= 90 ? 'var(--text-success)' : 'var(--text-warning)' }}>
            {coveragePct}%
          </span>
        </div>
        <div style={{ height: 7, background: 'var(--surface-1)', borderRadius: 99, overflow: 'hidden', marginTop: 10 }}>
          <div style={{ width: `${coveragePct}%`, height: '100%', borderRadius: 99,
            background: coveragePct >= 90 ? 'var(--text-success)' : 'var(--text-warning)' }} />
        </div>
        {priorityMissing.length > 0 && (
          <p style={{ fontSize: 11.5, color: 'var(--text-secondary)', margin: '9px 0 0' }}>
            ควรเริ่มจากเมนูขายดี: {priorityMissing.map((row) => row.name).join(', ')}
          </p>
        )}
      </div>

      {draftCount > 0 && (
        <div style={{
          background: '#fdf3e3', border: '1px solid var(--text-warning)', borderRadius: 8,
          padding: '10px 12px', marginBottom: 12, fontSize: 12, color: 'var(--text-warning)',
        }}>
          ระบบผูกไว้ให้ {draftCount} เมนู รอกรอกปริมาณ — ยังไม่มีผลกับสต๊อกจนกว่าจะเปิดกรอกและบันทึกทีละเมนู
        </div>
      )}

      <div className="card">
        {/* An empty card is not an empty state. With no menus this page
            was a heading, one sentence, and a blank rounded box - which
            says nothing about why it is empty or what to do next. */}
        {items.length === 0 && (
          <div style={{ padding: '26px 18px', textAlign: 'center' }}>
            <p style={{ fontSize: 13.5, fontWeight: 500, margin: '0 0 5px' }}>
              ยังไม่มีเมนูให้ผูกสูตร
            </p>
            <p style={{ fontSize: 12.5, color: 'var(--text-muted)', margin: 0, lineHeight: 1.6 }}>
              เมนูมาจาก Loyverse — เพิ่มเมนูในนั้นแล้วกลับมาที่หน้านี้
              <br />ถ้าเพิ่งเพิ่มไป ลองกด "อัปเดตข้อมูล" ที่หน้าแรกก่อน
            </p>
          </div>
        )}
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
                      : draft ? `ระบบผูกให้ ${draft.ingredients.length} วัตถุดิบ · รอกรอกปริมาณ`
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
                        title="ให้ระบบผูกวัตถุดิบให้อัตโนมัติ"
                        style={{ fontSize: 12, padding: '4px 8px' }}>
                        {suggesting === it.name ? '...' : '⚡'}
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
              {editingItem.from === 'ai' ? '⚡ ผูกโดยระบบ' : 'สูตรอาหาร'}
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
                ระบบเดาว่าน่าจะใช้วัตถุดิบเหล่านี้ — <b>ปริมาณต้องกรอกเอง</b> เพราะแต่ละร้านตักไม่เท่ากัน
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
                    ระบบเดา: {r.suggested_name}{r.suggested_unit ? ` (${r.suggested_unit})` : ''}
                  </p>
                )}
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  {/* Same picker as the delivery form: type to search, and
                      a name that doesn't exist yet can be added inline
                      instead of abandoning the recipe to go create it. */}
                  <MaterialPicker materials={materials} value={r.material_id} storeId={storeId}
                    onChange={(id) => updateRow(idx, { material_id: id })}
                    onCreated={async () => setMaterials(await api.getMaterials(storeId))} />
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
            {duplicateRows > 0 && (
              <p style={{ fontSize: 11, color: 'var(--text-warning)', margin: '8px 0 0' }}>
                ⚠ มีวัตถุดิบซ้ำ กรุณารวมปริมาณเป็นรายการเดียว
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
                  disabled={rows.length === 0 || incompleteRows > 0 || duplicateRows > 0}>
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
