import { useEffect, useState } from 'react';
import { useStore } from '../store/StoreContext';
import { api } from '../api/client';

export default function Receipts() {
  const { storeId } = useStore();
  const [receipts, setReceipts] = useState([]);
  const [search, setSearch] = useState('');
  const [viewing, setViewing] = useState(null);

  useEffect(() => {
    if (!storeId) return;
    api.getReceipts(storeId).then(setReceipts);
  }, [storeId]);

  if (!storeId) return <p>เลือกสาขาในหน้าตั้งค่าก่อน</p>;

  const visible = receipts.filter((r) => {
    const q = search.toLowerCase();
    return r.receipt_number?.toLowerCase().includes(q) ||
      r.line_items.some((li) => li.item_name?.toLowerCase().includes(q));
  });

  return (
    <div>
      <p style={{ fontSize: 15, fontWeight: 500, margin: '0 0 16px' }}>รายการบิล</p>
      <input placeholder="ค้นหาเลขบิลหรือเมนู" value={search} onChange={(e) => setSearch(e.target.value)}
        style={{ width: '100%', marginBottom: 16 }} />

      <div className="card">
        {visible.length === 0 && <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>ไม่พบบิล</p>}
        {visible.map((r, idx) => (
          <div key={r.receipt_number} style={{
            display: 'flex', alignItems: 'center', gap: 12, padding: '12px 0',
            borderBottom: idx < visible.length - 1 ? '0.5px solid var(--border)' : 'none',
          }}>
            <div style={{ flex: 1 }}>
              <p style={{ fontSize: 14, margin: 0 }}>#{r.receipt_number}</p>
              <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '2px 0 0' }}>{r.created_at}</p>
            </div>
            <p style={{ fontSize: 14, margin: 0 }}>฿{(r.total || 0).toLocaleString()}</p>
            <button onClick={() => setViewing(r)}>ดูรายละเอียด</button>
          </div>
        ))}
      </div>

      {viewing && (
        <div className="modal-overlay" onClick={() => setViewing(null)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 4px' }}>บิล #{viewing.receipt_number}</p>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '0 0 16px' }}>{viewing.created_at}</p>
            {viewing.line_items.map((li, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, padding: '6px 0', borderBottom: '0.5px solid var(--border)' }}>
                <span>{li.item_name} x{li.quantity}</span>
                <span>฿{((li.price || 0) * (li.quantity || 0)).toLocaleString()}</span>
              </div>
            ))}
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, fontWeight: 500, paddingTop: 10 }}>
              <span>รวม</span><span>฿{(viewing.total || 0).toLocaleString()}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
              <button style={{ background: 'var(--surface-1)' }} onClick={() => setViewing(null)}>ปิด</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
