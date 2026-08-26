import { useEffect, useState } from 'react';
import { useStore } from '../store/StoreContext';
import { api } from '../api/client';

const CATS = { fixed: 'ค่าใช้จ่ายคงที่', variable: 'ค่าใช้จ่ายผันแปร', material: 'ค่าวัตถุดิบ' };
const now = new Date();
const YEARS = [now.getFullYear() - 1, now.getFullYear()];
const MONTH_NAMES = ['มกราคม','กุมภาพันธ์','มีนาคม','เมษายน','พฤษภาคม','มิถุนายน',
  'กรกฎาคม','สิงหาคม','กันยายน','ตุลาคม','พฤศจิกายน','ธันวาคม'];

export default function IncomeExpense() {
  const { storeId } = useStore();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(String(now.getMonth())); // '' = whole year
  const [receipts, setReceipts] = useState([]);
  const [materials, setMaterials] = useState([]);
  const [recipes, setRecipes] = useState({});
  const [expenses, setExpenses] = useState({ fixed: [], variable: [], material: [] });
  // null = closed. {} = recording a new one. An expense = correcting that one.
  const [editing, setEditing] = useState(null);
  const [busyId, setBusyId] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (!storeId) return;
    api.getReceipts(storeId).then(setReceipts);
    api.getMaterials(storeId).then(setMaterials);
    ['fixed', 'variable', 'material'].forEach((c) =>
      api.getExpenses(storeId, c).then((list) => setExpenses((prev) => ({ ...prev, [c]: list }))));
  }, [storeId]);

  useEffect(() => {
    if (!storeId || receipts.length === 0) return;
    const names = new Set();
    receipts.forEach((r) => r.line_items.forEach((li) => names.add(li.item_name)));
    Promise.all([...names].map((n) => api.getRecipe(storeId, n).then((r) => [n, r])))
      .then((pairs) => setRecipes(Object.fromEntries(pairs)));
  }, [storeId, receipts]);

  if (!storeId) return <p>เลือกสาขาในหน้าตั้งค่าก่อน</p>;

  const inPeriod = (dateStr) => {
    const d = new Date(dateStr);
    if (isNaN(d)) return false;
    return d.getFullYear() === year && (month === '' || d.getMonth() === parseInt(month));
  };

  const periodReceipts = receipts.filter((r) => inPeriod(r.created_at));
  const income = periodReceipts.reduce((s, r) => s + (r.total || 0), 0);

  const materialCostByRecipe = periodReceipts.reduce((sum, r) => {
    r.line_items.forEach((li) => {
      const recipe = recipes[li.item_name] || [];
      recipe.forEach((ing) => {
        const mat = materials.find((m) => m.id === ing.material_id);
        if (mat) sum += (mat.cost || 0) * ing.qty * (li.quantity || 0);
      });
    });
    return sum;
  }, 0);

  const fixedInPeriod = expenses.fixed.filter((e) => inPeriod(e.date));
  const variableInPeriod = expenses.variable.filter((e) => inPeriod(e.date));
  const materialInPeriod = expenses.material.filter((e) => inPeriod(e.date));
  const fixedSum = fixedInPeriod.reduce((s, e) => s + e.amount, 0);
  const variableSum = variableInPeriod.reduce((s, e) => s + e.amount, 0);
  const materialSum = materialInPeriod.reduce((s, e) => s + e.amount, 0);
  const totalExpense = fixedSum + variableSum + materialSum;

  async function reloadExpenses() {
    const lists = await Promise.all(
      ['fixed', 'variable', 'material'].map((c) => api.getExpenses(storeId, c)));
    setExpenses({ fixed: lists[0], variable: lists[1], material: lists[2] });
  }

  async function saveExpense(form) {
    setError('');
    try {
      if (editing?.id) await api.updateExpense(storeId, editing.id, form);
      else await api.addExpense(storeId, form);
      // Every category, not just this one: a correction can move an entry
      // from one category to another, and refreshing only the new one
      // would leave a stale copy sitting in the old.
      await reloadExpenses();
      setEditing(null);
    } catch (e) {
      setError(e.message);
    }
  }

  async function removeExpense(expense) {
    // Named and priced in the question. "ลบรายการนี้?" is not enough to
    // decide on when three rows look alike, and this one does not come back.
    const ok = window.confirm(
      `ลบรายจ่าย "${expense.name}" ฿${Number(expense.amount).toLocaleString()} ` +
      `(${expense.date})?\n\nลบแล้วหายถาวร กู้คืนไม่ได้`);
    if (!ok) return;

    setBusyId(expense.id);
    setError('');
    try {
      await api.deleteExpense(storeId, expense.id);
      await reloadExpenses();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId('');
    }
  }

  const allExpenses = [
    ...fixedInPeriod.map((e) => ({ ...e, category: 'fixed' })),
    ...variableInPeriod.map((e) => ({ ...e, category: 'variable' })),
    ...materialInPeriod.map((e) => ({ ...e, category: 'material' })),
  ].sort((a, b) => (b.date || '').localeCompare(a.date || ''));
  const isCurrentMonth = month !== '' && year === now.getFullYear() && parseInt(month) === now.getMonth();

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
        <p style={{ fontSize: 15, fontWeight: 500, margin: 0 }}>รายรับรายจ่าย</p>
        <div style={{ display: 'flex', gap: 8 }}>
          <select value={year} onChange={(e) => setYear(parseInt(e.target.value))}>
            {YEARS.map((y) => <option key={y} value={y}>{y + 543}</option>)}
          </select>
          <select value={month} onChange={(e) => setMonth(e.target.value)}>
            <option value="">ทั้งปี</option>
            {MONTH_NAMES.map((m, i) => <option key={m} value={i}>{m}</option>)}
          </select>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, marginBottom: 12 }}>
        <Stat label="รายรับ" value={income} />
        <Stat label="ค่าใช้จ่ายรวม" value={totalExpense} />
        <Stat label="กำไรสุทธิ" value={income - totalExpense} color="var(--text-success)" />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, marginBottom: 24 }}>
        <Stat label="ค่าใช้จ่ายคงที่" value={fixedSum} small />
        <Stat label="ค่าใช้จ่ายผันแปร" value={variableSum} small />
        <Stat label="ค่าวัตถุดิบ" value={materialSum} small />
      </div>

      {/* One list instead of three tabs. The categories were never
          separate things to work on - they're a label on each entry - and
          splitting them meant three places to look for "what did I spend
          this month". The category is chosen when recording instead. */}
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
          <p style={{ fontSize: 14, fontWeight: 500, margin: 0 }}>รายจ่ายทั้งหมด</p>
          {isCurrentMonth && <button onClick={() => setEditing({})}>+ บันทึกรายจ่าย</button>}
        </div>
        <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 12px' }}>
          ค่าวัตถุดิบระบบคำนวณให้เองจากสูตร × ยอดขาย ไม่ต้องกรอก
        </p>

        {allExpenses.length === 0 && (
          <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>ยังไม่มีรายการ</p>
        )}
        {allExpenses.map((e, idx) => (
          <div key={e.id || `${e.category}-${idx}`} style={{
            display: 'flex', gap: 8, alignItems: 'center', padding: '9px 0',
            borderBottom: idx < allExpenses.length - 1 ? '0.5px solid var(--border)' : 'none',
          }}>
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{ fontSize: 13, display: 'block' }}>{e.name}</span>
              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                {CATS[e.category]} · {e.date}
              </span>
            </span>
            <span style={{ fontSize: 13, fontWeight: 500 }}>฿{e.amount.toLocaleString()}</span>
            {/* Correcting is allowed in any month, unlike recording. A
                wrong number from last month stays wrong until someone
                fixes it - and it is still in the profit figure while it
                does. Entries with no id are pre-existing data from before
                these were addressable; they can still be read. */}
            {e.id && (
              <span style={{ display: 'flex', gap: 4, flex: 'none' }}>
                <button onClick={() => setEditing(e)} disabled={busyId === e.id}
                  style={{ fontSize: 11, padding: '4px 8px' }}>แก้ไข</button>
                <button onClick={() => removeExpense(e)} disabled={busyId === e.id}
                  style={{ fontSize: 11, padding: '4px 8px', color: 'var(--text-danger)' }}>
                  {busyId === e.id ? '...' : 'ลบ'}
                </button>
              </span>
            )}
          </div>
        ))}

        {error && (
          <p style={{ fontSize: 12, color: 'var(--text-danger)', margin: '10px 0 0' }}>{error}</p>
        )}
      </div>

      <div className="stat-card" style={{ marginTop: 16 }}>
        <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '0 0 8px' }}>
          เทียบยอดซื้อวัตถุดิบจริง กับต้นทุนตามสูตร
        </p>
        <Row label="ซื้อจริง" value={materialSum} />
        <Row label="ตามสูตรควรใช้" value={materialCostByRecipe} />
        <Row label="ส่วนต่าง" value={materialSum - materialCostByRecipe} bold warn />
      </div>

      {editing && (
        <ExpenseModal expense={editing} onCancel={() => setEditing(null)}
          onSave={saveExpense} />
      )}
    </div>
  );
}

function Stat({ label, value, color, small }) {
  return (
    <div className="stat-card">
      <p style={{ fontSize: small ? 12 : 13, color: 'var(--text-secondary)', margin: '0 0 6px' }}>{label}</p>
      <p style={{ fontSize: small ? 18 : 24, fontWeight: 500, margin: 0, color }}>฿{Math.round(value).toLocaleString()}</p>
    </div>
  );
}
function Row({ label, value, bold, warn }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 4, fontWeight: bold ? 500 : 400 }}>
      <span>{label}</span><span style={{ color: warn ? 'var(--text-warning)' : undefined }}>
        {value >= 0 ? '' : '-'}฿{Math.abs(Math.round(value)).toLocaleString()}
      </span>
    </div>
  );
}
/**
 * Records a new expense, or corrects one that was typed wrong.
 *
 * The same form for both on purpose: correcting is not a different job
 * from recording, it is the same job done again with the right numbers,
 * and a separate screen for it would be one more thing to keep in step.
 */
function ExpenseModal({ expense, onCancel, onSave }) {
  const isEdit = !!expense.id;
  const [category, setCategory] = useState(expense.category || 'fixed');
  const [name, setName] = useState(expense.name || '');
  const [amount, setAmount] = useState(expense.amount ?? '');
  const [date, setDate] = useState(expense.date || new Date().toISOString().slice(0, 10));
  const [saving, setSaving] = useState(false);

  const valid = name.trim() && parseFloat(amount) > 0 && date;

  async function submit() {
    if (!valid || saving) return;
    setSaving(true);
    try {
      await onSave({ category, name: name.trim(), amount: parseFloat(amount) || 0, date });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 16px' }}>
          {isEdit ? 'แก้ไขรายจ่าย' : 'บันทึกรายจ่าย'}
        </p>

        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>หมวด</label>
        {/* ค่าวัตถุดิบ is deliberately absent when recording: it's
            computed from deliveries already recorded, so letting someone
            type it here would double-count the same spend against itself.
            It IS offered when correcting an entry that is already in that
            category, because dropping the option would silently move the
            entry somewhere else the moment anyone edited its name. */}
        <select value={category} onChange={(e) => setCategory(e.target.value)}
          style={{ width: '100%', margin: '4px 0 4px' }}>
          <option value="fixed">{CATS.fixed}</option>
          <option value="variable">{CATS.variable}</option>
          {expense.category === 'material' && (
            <option value="material">{CATS.material}</option>
          )}
        </select>
        <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 12px' }}>
          {category === 'material'
            ? 'ปกติระบบคำนวณให้เองจากสูตร × ยอดขาย - รายการนี้บันทึกไว้ก่อนหน้านั้น'
            : category === 'fixed'
              ? 'จ่ายเท่าเดิมทุกเดือน เช่น ค่าเช่า ค่าเน็ต เงินเดือนประจำ'
              : 'จ่ายไม่เท่ากันแต่ละเดือน เช่น ค่าไฟ ค่าแก๊ส ค่าล่วงเวลา'}
        </p>

        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>รายการ</label>
        <input value={name} onChange={(e) => setName(e.target.value)} style={{ width: '100%', margin: '4px 0 12px' }} />
        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>จำนวนเงิน</label>
        <input type="number" value={amount} onChange={(e) => setAmount(e.target.value)} style={{ width: '100%', margin: '4px 0 12px' }} />
        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>วันที่จ่าย</label>
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} style={{ width: '100%', margin: '4px 0 16px' }} />
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onCancel} disabled={saving}>ยกเลิก</button>
          <button style={{ background: 'var(--surface-1)' }}
            onClick={submit} disabled={!valid || saving}>
            {saving ? 'กำลังบันทึก...' : 'บันทึก'}
          </button>
        </div>
      </div>
    </div>
  );
}
