import { useEffect, useState } from 'react';
import { api } from '../api/client';

/**
 * Our own back office - how many businesses use the system, how many are
 * still active. Read-only on purpose: there is no endpoint here that opens
 * a customer's stock, recipes, or takings. If we could read their books,
 * "your data is your own" would be a marketing line rather than a fact.
 */
export default function Admin() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api.adminOverview().then(setData).catch((e) => setError(e.message));
  }, []);

  if (error) return <p style={{ fontSize: 13, color: 'var(--text-danger)' }}>{error}</p>;
  if (!data) return <p style={{ fontSize: 13 }}>กำลังโหลด...</p>;

  return (
    <div>
      <p style={{ fontSize: 15, fontWeight: 500, margin: '0 0 4px' }}>ภาพรวมระบบ</p>
      <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 16px' }}>
        ดูได้อย่างเดียว - ไม่มีทางเข้าถึงข้อมูลภายในของแต่ละธุรกิจจากหน้านี้
      </p>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
        <StatCard label="ธุรกิจทั้งหมด" value={data.tenant_count} />
        <StatCard label="ผู้ใช้ทั้งหมด" value={data.user_count} />
        <StatCard label="ใช้งานใน 7 วัน" value={data.active_7d} />
      </div>

      <div className="card">
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 12px' }}>
          รายชื่อธุรกิจ ({data.tenants.length})
        </p>
        {data.tenants.length === 0 && (
          <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: 0 }}>ยังไม่มีธุรกิจสมัครใช้งาน</p>
        )}
        {data.tenants.map((t, idx) => (
          <div key={t.id} style={{
            display: 'flex', alignItems: 'center', gap: 12, padding: '10px 0',
            borderBottom: idx < data.tenants.length - 1 ? '0.5px solid var(--border)' : 'none',
          }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <p style={{ fontSize: 14, margin: 0 }}>{t.name || '(ไม่มีชื่อ)'}</p>
              <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '2px 0 0' }}>
                สมัคร {formatDate(t.created_at)} · ใช้งานล่าสุด {t.last_active_date || '-'}
              </p>
            </div>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              {t.user_count} ผู้ใช้
            </span>
            <span style={{
              fontSize: 11,
              color: t.loyverse_connected ? 'var(--text-success)' : 'var(--text-muted)',
            }}>
              {t.loyverse_connected ? 'เชื่อม Loyverse' : 'ยังไม่เชื่อม'}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function StatCard({ label, value }) {
  return (
    <div className="card" style={{ flex: '1 1 140px', minWidth: 140 }}>
      <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 4px' }}>{label}</p>
      <p style={{ fontSize: 24, fontWeight: 500, margin: 0 }}>{value}</p>
    </div>
  );
}

function formatDate(iso) {
  if (!iso) return '-';
  try {
    return new Date(iso).toLocaleDateString('th-TH', { year: 'numeric', month: 'short', day: 'numeric' });
  } catch {
    return iso;
  }
}
