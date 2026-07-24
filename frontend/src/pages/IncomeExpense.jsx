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
  const [tab, setTab] = useState('fixed');
  const [receipts, setReceipts] = useState([]);
  const [materials, setMaterials] = useState([]);
  const [recipes, setRecipes] = useState({});
  const [expenses, setExpenses] = useState({ fixed: [], variable: [], material: [] });
  const [showAdd, setShowAdd] = useState(false);

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

  async function saveExpense(form) {
    await api.addExpense(storeId, form);
    const updated = await api.getExpenses(storeId, form.category);
    setExpenses((prev) => ({ ...prev, [form.category]: updated }));
    setShowAdd(false);
  }

  const listForTab = tab === 'fixed' ? fixedInPeriod : tab === 'variable' ? variableInPeriod : materialInPeriod;
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

      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        {Object.entries(CATS).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)} style={{ background: tab === k ? 'var(--surface-1)' : undefined }}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'material' && (
        <div className="stat-card" style={{ marginBottom: 16 }}>
          <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '0 0 8px' }}>เทียบยอดซื้อจริงกับต้นทุนตามสูตร</p>
          <Row label="ซื้อจริง" value={materialSum} />
          <Row label="ตามสูตรควรใช้" value={materialCostByRecipe} />
          <Row label="ส่วนต่าง" value={materialSum - materialCostByRecipe} bold warn />
        </div>
      )}

      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
          <p style={{ fontSize: 14, fontWeight: 500, margin: 0 }}>{CATS[tab]}</p>
          {isCurrentMonth && <button onClick={() => setShowAdd(true)}>+ บันทึกรายจ่าย</button>}
        </div>
        {listForTab.length === 0 && <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>ยังไม่มีรายการ</p>}
        {listForTab.map((e, idx) => (
          <div key={e.id || idx} style={{
            display: 'flex', gap: 8, padding: '8px 0',
            borderBottom: idx < listForTab.length - 1 ? '0.5px solid var(--border)' : 'none',
          }}>
            <span style={{ flex: 1, fontSize: 13 }}>{e.name}</span>
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{e.date}</span>
            <span style={{ fontSize: 13 }}>฿{e.amount.toLocaleString()}</span>
          </div>
        ))}
      </div>

      {showAdd && (
        <AddExpenseModal category={tab} onCancel={() => setShowAdd(false)} onSave={saveExpense} />
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
function AddExpenseModal({ category, onCancel, onSave }) {
  const [name, setName] = useState('');
  const [amount, setAmount] = useState('');
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 16px' }}>บันทึกรายจ่าย ({CATS[category]})</p>
        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>รายการ</label>
        <input value={name} onChange={(e) => setName(e.target.value)} style={{ width: '100%', margin: '4px 0 12px' }} />
        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>จำนวนเงิน</label>
        <input type="number" value={amount} onChange={(e) => setAmount(e.target.value)} style={{ width: '100%', margin: '4px 0 12px' }} />
        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>วันที่จ่าย</label>
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} style={{ width: '100%', margin: '4px 0 16px' }} />
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onCancel}>ยกเลิก</button>
          <button style={{ background: 'var(--surface-1)' }}
            onClick={() => onSave({ category, name, amount: parseFloat(amount) || 0, date })}>บันทึก</button>
        </div>
      </div>
    </div>
  );
}
