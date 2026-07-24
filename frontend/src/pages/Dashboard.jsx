import { useEffect, useState } from 'react';
import { useStore } from '../store/StoreContext';
import { api } from '../api/client';
import BarChart from '../components/BarChart';

export default function Dashboard() {
  const { storeId } = useStore();
  const [receipts, setReceipts] = useState([]);
  const [materials, setMaterials] = useState([]);
  const [recipes, setRecipes] = useState({}); // { itemName: [{material_id, qty}] }
  const [period, setPeriod] = useState('today');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!storeId) return;
    setLoading(true);
    Promise.all([api.getReceipts(storeId), api.getMaterials(storeId)]).then(async ([rec, mats]) => {
      setReceipts(rec);
      setMaterials(mats);
      const names = new Set();
      rec.forEach((r) => r.line_items.forEach((li) => names.add(li.item_name)));
      const pairs = await Promise.all(
        [...names].map((n) => api.getRecipe(storeId, n).then((r) => [n, r]))
      );
      setRecipes(Object.fromEntries(pairs));
      setLoading(false);
    }).catch(() => setLoading(false));
  }, [storeId]);

  if (!storeId) return <p>เลือกสาขาในหน้าตั้งค่าก่อน</p>;
  if (loading) return <p>กำลังโหลด...</p>;

  const now = new Date();
  const inPeriod = (d) => {
    if (period === 'today') return d.toDateString() === now.toDateString();
    if (period === 'week') {
      const weekAgo = new Date(now); weekAgo.setDate(now.getDate() - 7);
      return d >= weekAgo;
    }
    return d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear();
  };
  const filtered = receipts.filter((r) => inPeriod(new Date(r.created_at)));

  const totalSales = filtered.reduce((s, r) => s + (r.total || 0), 0);
  const billCount = filtered.length;

  const itemTotals = {};
  filtered.forEach((r) => r.line_items.forEach((li) => {
    const key = li.item_name || 'ไม่ระบุ';
    itemTotals[key] = itemTotals[key] || { qty: 0, revenue: 0 };
    itemTotals[key].qty += li.quantity || 0;
    itemTotals[key].revenue += (li.price || 0) * (li.quantity || 0);
  }));
  const topItems = Object.entries(itemTotals).sort((a, b) => b[1].revenue - a[1].revenue).slice(0, 5);
  const missingRecipeItems = Object.keys(itemTotals).filter((name) => (recipes[name] || []).length === 0);

  const materialCost = filtered.reduce((sum, r) => {
    r.line_items.forEach((li) => {
      (recipes[li.item_name] || []).forEach((ing) => {
        const mat = materials.find((m) => m.id === ing.material_id);
        if (mat) sum += (mat.cost || 0) * ing.qty * (li.quantity || 0);
      });
    });
    return sum;
  }, 0);
  const grossProfit = totalSales - materialCost;
  const lowStockCount = materials.filter((m) => (m.stock || 0) <= (m.par || 0)).length;

  let chartLabels = [];
  let chartValues = [];
  if (period === 'today') {
    const buckets = Array(12).fill(0);
    filtered.forEach((r) => {
      const h = new Date(r.created_at).getHours();
      buckets[Math.floor(h / 2)] += r.total || 0;
    });
    chartLabels = buckets.map((_, i) => `${i * 2}-${i * 2 + 2}`);
    chartValues = buckets;
  } else {
    const days = period === 'week' ? 7 : now.getDate();
    const buckets = Array(days).fill(0);
    const start = new Date(now);
    if (period === 'week') start.setDate(now.getDate() - (days - 1));
    else start.setDate(1);
    filtered.forEach((r) => {
      const d = new Date(r.created_at);
      const idx = Math.floor((d - start) / (1000 * 60 * 60 * 24));
      if (idx >= 0 && idx < days) buckets[idx] += r.total || 0;
    });
    chartLabels = buckets.map((_, i) => {
      const d = new Date(start); d.setDate(start.getDate() + i);
      return String(d.getDate());
    });
    chartValues = buckets;
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
        <p style={{ fontSize: 15, fontWeight: 500, margin: 0 }}>ภาพรวมยอดขาย</p>
        <div style={{ display: 'flex', gap: 8 }}>
          {['today', 'week', 'month'].map((p) => (
            <button key={p} onClick={() => setPeriod(p)}
              style={{ background: period === p ? 'var(--surface-1)' : undefined }}>
              {p === 'today' ? 'วันนี้' : p === 'week' ? 'สัปดาห์นี้' : 'เดือนนี้'}
            </button>
          ))}
        </div>
      </div>

      {missingRecipeItems.length > 0 && (
        <div style={{
          background: '#fdf3e3', border: '1px solid var(--text-warning)', borderRadius: 8,
          padding: '10px 14px', marginBottom: 16, fontSize: 13, color: 'var(--text-warning)',
        }}>
          ⚠ เมนูที่ขายในช่วงนี้ยังไม่ได้ผูกสูตร: {missingRecipeItems.join(', ')} — ต้นทุนและกำไรที่คำนวณไว้ยังไม่นับรวมเมนูนี้
          (ไปผูกสูตรได้ที่หน้า "สูตรอาหาร")
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, marginBottom: 24 }}>
        <Stat label="ยอดขาย" value={`฿${totalSales.toLocaleString()}`} />
        <Stat label="จำนวนบิล" value={billCount} />
        <Stat label="กำไรขั้นต้น" value={`฿${Math.round(grossProfit).toLocaleString()}`} color="var(--text-success)" />
        <Stat label="สต๊อกใกล้หมด" value={lowStockCount} color={lowStockCount > 0 ? 'var(--text-warning)' : undefined} />
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 12px' }}>ยอดขายรวม</p>
        <BarChart labels={chartLabels} values={chartValues} />
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 12px' }}>ยอดขายแยกรายการ</p>
        {topItems.length === 0 && <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>ยังไม่มียอดขายในช่วงนี้</p>}
        {topItems.map(([name, t], idx) => (
          <div key={name} style={{
            display: 'flex', justifyContent: 'space-between', padding: '8px 0', fontSize: 13,
            borderBottom: idx < topItems.length - 1 ? '0.5px solid var(--border)' : 'none',
          }}>
            <span>{name}{(recipes[name] || []).length === 0 && <span style={{ color: 'var(--text-warning)' }}> ⚠</span>}</span>
            <span style={{ color: 'var(--text-secondary)' }}>x{t.qty}</span>
            <span>฿{t.revenue.toLocaleString()}</span>
          </div>
        ))}
      </div>

      <div className="card">
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 12px' }}>บิลล่าสุด</p>
        {filtered.slice(0, 5).map((r, idx) => (
          <div key={r.receipt_number} style={{
            display: 'flex', justifyContent: 'space-between', padding: '8px 0', fontSize: 13,
            borderBottom: idx < 4 ? '0.5px solid var(--border)' : 'none',
          }}>
            <span>#{r.receipt_number}</span>
            <span style={{ color: 'var(--text-secondary)' }}>
              {r.line_items.map((li) => li.item_name).join(', ')}
            </span>
            <span>฿{(r.total || 0).toLocaleString()}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Stat({ label, value, color }) {
  return (
    <div className="stat-card">
      <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '0 0 6px' }}>{label}</p>
      <p style={{ fontSize: 24, fontWeight: 500, margin: 0, color }}>{value}</p>
    </div>
  );
}
