import { useCallback, useEffect, useMemo, useState } from 'react';
import { useStore } from '../store/StoreContext';
import { api } from '../api/client';
import { newId } from '../util/ids';

/**
 * Orders that never went through the till.
 *
 * A shop selling on Grab keeps these in a paper notebook: the
 * ingredients leave the kitchen and nothing records it, so every
 * delivery order makes the variance report a little more wrong while
 * looking exactly as confident as before. Recorded here they become
 * ordinary sales - counted in the takings, deducted through the same
 * recipes as a walk-in.
 *
 * The price is the one the customer paid on the platform, not the
 * shop's own menu price. The platform's cut is a separate cost and
 * belongs in รายรับรายจ่าย, where its monthly invoice already goes.
 */

const baht = (n) => '฿' + Math.round(n).toLocaleString('en-US');

function localInput(date = new Date()) {
  // datetime-local wants the shop's own clock, not UTC.
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date - offset).toISOString().slice(0, 16);
}

function dayWindow(dateStr) {
  const start = new Date(dateStr + 'T00:00:00');
  const end = new Date(dateStr + 'T23:59:59.999');
  return { from: start.toISOString(), to: end.toISOString() };
}

export default function DeliveryOrders() {
  const { storeId } = useStore();
  const [day, setDay] = useState(() => new Date().toISOString().slice(0, 10));
  const [channels, setChannels] = useState({});
  const [orders, setOrders] = useState([]);
  const [items, setItems] = useState([]);
  const [adding, setAdding] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState('');
  const [error, setError] = useState('');
  const [note, setNote] = useState(null);

  const load = useCallback(async () => {
    if (!storeId) return;
    setLoading(true);
    setError('');
    try {
      const { from, to } = dayWindow(day);
      const res = await api.getDeliveryOrders(storeId, from, to);
      setChannels(res.channels || {});
      setOrders(res.orders || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [storeId, day]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!storeId) return;
    // The menu as the till knows it. Using these names is what connects
    // an order to its recipe - a name typed by hand matches nothing.
    api.getItems(storeId).then(setItems).catch(() => setItems([]));
  }, [storeId]);

  const dayTotal = orders.reduce((sum, o) => sum + (o.total || 0), 0);

  async function saveOrder(order) {
    setError('');
    const res = await api.addDeliveryOrder(storeId, order);
    setAdding(false);
    // Said out loud rather than left to be noticed: a dish with no
    // recipe records its money and moves no stock, which looks identical
    // to a bug from the outside.
    setNote(res.no_recipe?.length
      ? { kind: 'warn', text: `บันทึกแล้ว แต่ ${res.no_recipe.join(', ')} ยังไม่มีสูตร จึงไม่ได้ตัดสต๊อก` }
      : { kind: 'ok', text: `บันทึกแล้ว · ตัดสต๊อก ${res.deducted_materials} รายการ` });
    await load();
  }

  async function removeOrder(order) {
    const ok = window.confirm(
      `ลบออเดอร์ ${channels[order.source] || order.source} ${baht(order.total || 0)}?\n\n` +
      'วัตถุดิบที่ตัดไปจะถูกคืนกลับเข้าสต๊อกให้อัตโนมัติ');
    if (!ok) return;
    setBusyId(order.receipt_number);
    setError('');
    try {
      const res = await api.deleteDeliveryOrder(storeId, order.receipt_number);
      setNote({ kind: 'ok', text: `ลบแล้ว · คืนสต๊อก ${res.returned_materials} รายการ` });
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId('');
    }
  }

  if (!storeId) return <p>เลือกสาขาในหน้าตั้งค่าก่อน</p>;

  return (
    <div>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
        gap: 12, flexWrap: 'wrap', marginBottom: 4,
      }}>
        <div>
          <p style={{ fontSize: 15, fontWeight: 500, margin: 0 }}>ออเดอร์นอกร้าน</p>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '4px 0 0', maxWidth: 460 }}>
            ออเดอร์ Grab โทรสั่ง หรือจากเมนูออนไลน์ ที่ไม่ได้กดขายผ่าน POS —
            บันทึกที่นี่แล้วระบบจะตัดสต๊อกตามสูตรและนับเป็นยอดขายให้เหมือนขายหน้าร้าน
          </p>
        </div>
        <input type="date" value={day} onChange={(e) => setDay(e.target.value)}
          style={{ fontSize: 12 }} />
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div style={{
          display: 'flex', justifyContent: 'space-between',
          alignItems: 'center', marginBottom: 12, gap: 12,
        }}>
          <span style={{ fontSize: 13 }}>
            {orders.length} ออเดอร์ · <strong>{baht(dayTotal)}</strong>
          </span>
          <button onClick={() => { setNote(null); setAdding(true); }}>+ บันทึกออเดอร์</button>
        </div>

        {note && (
          <p style={{
            fontSize: 12, margin: '0 0 10px',
            color: note.kind === 'warn' ? 'var(--text-warning)' : 'var(--text-success)',
          }}>{note.text}</p>
        )}
        {error && <p style={{ fontSize: 12, color: 'var(--text-danger)', margin: '0 0 10px' }}>{error}</p>}
        {loading && <p style={{ fontSize: 13 }}>กำลังโหลด...</p>}

        {!loading && orders.length === 0 && (
          <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: 0 }}>
            ยังไม่มีออเดอร์นอกร้านในวันนี้
          </p>
        )}

        {orders.map((o, idx) => (
          <div key={o.receipt_number} style={{
            padding: '10px 0',
            borderTop: idx > 0 ? '0.5px solid var(--border)' : 'none',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ fontSize: 13, fontWeight: 500 }}>
                  {channels[o.source] || o.source}
                </span>
                <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 8 }}>
                  {new Date(o.date).toLocaleTimeString('th-TH',
                    { hour: '2-digit', minute: '2-digit' })}
                </span>
              </span>
              <span style={{ fontSize: 13, fontWeight: 600 }}>{baht(o.total || 0)}</span>
              <button onClick={() => removeOrder(o)} disabled={busyId === o.receipt_number}
                style={{ fontSize: 11, padding: '4px 8px', color: 'var(--text-danger)' }}>
                {busyId === o.receipt_number ? '...' : 'ลบ'}
              </button>
            </div>
            <p style={{ fontSize: 11.5, color: 'var(--text-muted)', margin: '3px 0 0' }}>
              {(o.items || []).map((i) => `${i.name} x${i.qty}`).join(' · ')}
              {o.note && ` · ${o.note}`}
            </p>
          </div>
        ))}
      </div>

      {adding && (
        <OrderModal channels={channels} items={items}
          onCancel={() => setAdding(false)} onSave={saveOrder} />
      )}
    </div>
  );
}

function OrderModal({ channels, items, onCancel, onSave }) {
  const [source, setSource] = useState('grab');
  const [when, setWhen] = useState(() => localInput());
  const [lines, setLines] = useState([]);
  const [search, setSearch] = useState('');
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const matches = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return items.slice(0, 8);
    return items.filter((i) => (i.name || '').toLowerCase().includes(q)).slice(0, 8);
  }, [items, search]);

  const total = lines.reduce((s, l) => s + l.qty * l.price, 0);

  function addLine(item) {
    setLines((current) => {
      const found = current.find((l) => l.name === item.name);
      if (found) {
        return current.map((l) => l.name === item.name ? { ...l, qty: l.qty + 1 } : l);
      }
      // The till's price is the starting point, not the answer: the
      // platform's menu price is usually higher, and what matters is the
      // money that actually arrived.
      return [...current, { name: item.name, qty: 1, price: item.price ?? 0 }];
    });
    setSearch('');
  }

  function setLine(name, patch) {
    setLines((current) => current.map((l) => l.name === name ? { ...l, ...patch } : l));
  }

  async function submit() {
    if (!lines.length || saving) return;
    setSaving(true);
    setError('');
    try {
      await onSave({
        order_id: newId(source),
        source,
        date: new Date(when).toISOString(),
        note,
        items: lines.map((l) => ({ name: l.name, qty: l.qty, price: l.price })),
      });
    } catch (e) {
      setError(e.message);
      setSaving(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 14px' }}>บันทึกออเดอร์นอกร้าน</p>

        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>ช่องทาง</label>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, margin: '6px 0 14px' }}>
          {Object.entries(channels).map(([key, label]) => (
            <button key={key} onClick={() => setSource(key)}
              style={{
                fontSize: 12, padding: '6px 11px',
                background: source === key ? 'var(--surface-2)' : 'transparent',
                fontWeight: source === key ? 600 : 400,
              }}>{label}</button>
          ))}
        </div>

        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>เมนู</label>
        <input value={search} onChange={(e) => setSearch(e.target.value)}
          placeholder="พิมพ์ค้นหาเมนู" style={{ width: '100%', margin: '4px 0 6px' }} />
        {matches.length > 0 && (
          <div style={{
            border: '1px solid var(--border)', borderRadius: 8,
            marginBottom: 12, maxHeight: 132, overflowY: 'auto',
          }}>
            {matches.map((item) => (
              <div key={item.id} onClick={() => addLine(item)} style={{
                display: 'flex', justifyContent: 'space-between', gap: 8,
                padding: '8px 11px', fontSize: 12.5, cursor: 'pointer',
              }}>
                <span style={{ minWidth: 0 }}>{item.name}</span>
                <span style={{ color: 'var(--text-muted)', flex: 'none' }}>
                  {item.price != null ? baht(item.price) : '—'}
                </span>
              </div>
            ))}
          </div>
        )}
        {items.length === 0 && (
          <p style={{ fontSize: 11.5, color: 'var(--text-warning)', margin: '0 0 12px' }}>
            ยังโหลดเมนูจาก Loyverse ไม่ได้ — ต้องใช้ชื่อเมนูเดียวกับใน POS สูตรถึงจะตัดสต๊อกถูก
          </p>
        )}

        {lines.map((l) => (
          <div key={l.name} style={{
            display: 'flex', alignItems: 'center', gap: 8, padding: '7px 0',
            borderTop: '0.5px solid var(--border)',
          }}>
            <span style={{ flex: 1, minWidth: 0, fontSize: 12.5 }}>{l.name}</span>
            <input type="number" min="1" value={l.qty}
              onChange={(e) => setLine(l.name, { qty: parseFloat(e.target.value) || 0 })}
              style={{ width: 52, fontSize: 12 }} aria-label={`จำนวน ${l.name}`} />
            <input type="number" min="0" value={l.price}
              onChange={(e) => setLine(l.name, { price: parseFloat(e.target.value) || 0 })}
              style={{ width: 68, fontSize: 12 }} aria-label={`ราคา ${l.name}`} />
            <button onClick={() => setLines((c) => c.filter((x) => x.name !== l.name))}
              style={{ fontSize: 11, padding: '4px 7px' }}>ลบ</button>
          </div>
        ))}

        {lines.length > 0 && (
          <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '8px 0 0' }}>
            ใส่ราคาที่ลูกค้าจ่ายบนแอป (ปกติสูงกว่าราคาหน้าร้าน) ค่าคอมมิชชั่นบันทึกเป็น
            รายจ่ายรายเดือนในหน้ารายรับรายจ่าย
          </p>
        )}

        <div style={{ display: 'flex', gap: 8, margin: '14px 0 0' }}>
          <div style={{ flex: 1 }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>วันเวลาที่ขาย</label>
            <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)}
              style={{ width: '100%', margin: '4px 0 0', fontSize: 12 }} />
          </div>
        </div>

        <label style={{ fontSize: 12, color: 'var(--text-secondary)', display: 'block', marginTop: 10 }}>
          หมายเหตุ (ไม่ใส่ก็ได้)
        </label>
        <input value={note} onChange={(e) => setNote(e.target.value)}
          placeholder="เช่น เลขออเดอร์บนแอป" style={{ width: '100%', margin: '4px 0 14px' }} />

        {error && <p style={{ fontSize: 12, color: 'var(--text-danger)', margin: '0 0 10px' }}>{error}</p>}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'space-between', alignItems: 'center' }}>
          <strong style={{ fontSize: 14 }}>{baht(total)}</strong>
          <span style={{ display: 'flex', gap: 8 }}>
            <button onClick={onCancel} disabled={saving}>ยกเลิก</button>
            <button style={{ background: 'var(--surface-1)' }}
              onClick={submit} disabled={!lines.length || saving}>
              {saving ? 'กำลังบันทึก...' : 'บันทึก'}
            </button>
          </span>
        </div>
      </div>
    </div>
  );
}
