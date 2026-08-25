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
  const [expenses, setExpenses] = useState({ fixed: [], variable: [] });
  const [receivings, setReceivings] = useState([]);
  const [showAdd, setShowAdd] = useState(false);

  useEffect(() => {
    if (!storeId) return;
    api.getReceipts(storeId).then(setReceipts);
    api.getMaterials(storeId).then(setMaterials);
    // Deliveries ARE the raw-material spend. The old code read an
    // expenses category called "material" that nothing ever wrote to, so
    // this line always came out ฿0 and net profit read far too high.
    api.getReceivings(storeId).then(setReceivings).catch(() => setReceivings([]));
    ['fixed', 'variable'].forEach((c) =>
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
  const materialInPeriod = receivings.filter((r) => inPeriod(r.date));
  const fixedSum = fixedInPeriod.reduce((s, e) => s + e.amount, 0);
  const variableSum = variableInPeriod.reduce((s, e) => s + e.amount, 0);
  const materialSum = materialInPeriod.reduce((s, r) => s + (r.total || 0), 0);
  const totalExpense = fixedSum + variableSum + materialSum;

  async function saveExpense(form) {
    await api.addExpense(storeId, form);
    const updated = await api.getExpenses(storeId, form.category);
    setExpenses((prev) => ({ ...prev, [form.category]: updated }));
    setShowAdd(false);
  }

  const allExpenses = [
    ...fixedInPeriod.map((e) => ({ ...e, category: 'fixed' })),
    ...variableInPeriod.map((e) => ({ ...e, category: 'variable' })),
    ...materialInPeriod.map((r) => ({
      id: r.id, category: 'material', date: r.date,
      name: r.supplier ? `ซื้อของจาก ${r.supplier}` : 'ซื้อของเข้าร้าน',
      amount: r.total || 0,
    })),
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
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, marginBottom: 16 }}>
        <Stat label="ค่าใช้จ่ายคงที่" value={fixedSum} small />
        <Stat label="ค่าใช้จ่ายผันแปร" value={variableSum} small />
      </div>

      {/* Two ways of counting raw materials, side by side.
          Only the first is in ค่าใช้จ่ายรวม above - it's money that
          actually left the till this month. The second is what the
          recipes say the food sold should have consumed. Adding both
          would count the same ingredients twice; showing only one would
          hide the more interesting number, which is the gap between them. */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 12, marginBottom: 24 }}>
        <div className="card" style={{ padding: '13px 14px' }}>
          <p style={{ fontSize: 11.5, color: 'var(--text-muted)', margin: 0 }}>
            รายจ่ายวัตถุดิบจริง
          </p>
          <p style={{ fontSize: 10.5, color: 'var(--text-muted)', margin: '1px 0 6px' }}>
            จากการบันทึกซื้อของ · นับในค่าใช้จ่ายรวม
          </p>
          <p style={{ fontSize: 21, fontWeight: 700, margin: 0, letterSpacing: -.3 }}>
            ฿{materialSum.toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </p>
          <p style={{ fontSize: 10.5, color: 'var(--text-muted)', margin: '4px 0 0' }}>
            {materialInPeriod.length} ครั้ง
          </p>
        </div>

        <div className="card" style={{ padding: '13px 14px' }}>
          <p style={{ fontSize: 11.5, color: 'var(--text-muted)', margin: 0 }}>
            ต้นทุนตามสูตร
          </p>
          <p style={{ fontSize: 10.5, color: 'var(--text-muted)', margin: '1px 0 6px' }}>
            คิดจากสูตร × ที่ขายไป · ไม่นับซ้ำในค่าใช้จ่ายรวม
          </p>
          <p style={{ fontSize: 21, fontWeight: 700, margin: 0, letterSpacing: -.3 }}>
            ฿{materialCostByRecipe.toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </p>
          <p style={{
            fontSize: 10.5, margin: '4px 0 0',
            color: materialSum - materialCostByRecipe > 0
              ? 'var(--text-warning)' : 'var(--text-muted)',
          }}>
            {materialSum >= materialCostByRecipe
              ? `ซื้อมากกว่าที่ขายไป ฿${(materialSum - materialCostByRecipe).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
              : `ขายมากกว่าที่ซื้อ ฿${(materialCostByRecipe - materialSum).toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
          </p>
        </div>
      </div>

      {/* One list instead of three tabs. The categories were never
          separate things to work on - they're a label on each entry - and
          splitting them meant three places to look for "what did I spend
          this month". The category is chosen when recording instead. */}
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
          <p style={{ fontSize: 14, fontWeight: 500, margin: 0 }}>รายจ่ายทั้งหมด</p>
          {isCurrentMonth && <button onClick={() => setShowAdd(true)}>+ บันทึกรายจ่าย</button>}
        </div>
        <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 12px' }}>
          ค่าวัตถุดิบมาจากหน้า "ซื้อของเข้าร้าน" อัตโนมัติ ไม่ต้องกรอกซ้ำ
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
          </div>
        ))}
      </div>

      <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '14px 2px 0', lineHeight: 1.6 }}>
        ตัวเลขสองอันนี้ไม่เท่ากันเป็นเรื่องปกติ — ซื้อของทีเดียวแล้วทยอยใช้หลายเดือนก็ทำให้ต่างกันได้
        ถ้าซื้อมากกว่าที่ขายไปมากผิดปกติติดกันหลายเดือน ลองดูที่หน้า "นับของ · ของหายไปไหน"
      </p>

      {showAdd && (
        <AddExpenseModal onCancel={() => setShowAdd(false)} onSave={saveExpense} />
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
function AddExpenseModal({ onCancel, onSave }) {
  const [category, setCategory] = useState('fixed');
  const [name, setName] = useState('');
  const [amount, setAmount] = useState('');
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 16px' }}>บันทึกรายจ่าย</p>

        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>หมวด</label>
        {/* ค่าวัตถุดิบ is deliberately absent: it's computed from
            deliveries already recorded, so letting someone type it here
            would double-count the same spend against itself. */}
        <select value={category} onChange={(e) => setCategory(e.target.value)}
          style={{ width: '100%', margin: '4px 0 4px' }}>
          <option value="fixed">{CATS.fixed}</option>
          <option value="variable">{CATS.variable}</option>
        </select>
        <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 12px' }}>
          {category === 'fixed'
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
          <button onClick={onCancel}>ยกเลิก</button>
          <button style={{ background: 'var(--surface-1)' }}
            onClick={() => onSave({ category, name, amount: parseFloat(amount) || 0, date })}>บันทึก</button>
        </div>
      </div>
    </div>
  );
}
