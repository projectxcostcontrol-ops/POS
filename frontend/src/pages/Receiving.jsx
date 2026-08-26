import { useEffect, useRef, useState } from 'react';
import { useStore } from '../store/StoreContext';
import { api } from '../api/client';
import { compressImage } from '../utils/imageCompress';
import MaterialPicker from '../components/MaterialPicker';
import { newId } from '../util/ids';

export default function Receiving() {
  const { storeId } = useStore();
  const [materials, setMaterials] = useState([]);
  const [receivings, setReceivings] = useState([]);
  const [drafts, setDrafts] = useState([]);
  const [showForm, setShowForm] = useState(false);
  // null = closed, {} = a new delivery, a receiving = correcting that one
  const [editingReceiving, setEditingReceiving] = useState(null);
  const [busyId, setBusyId] = useState('');
  const [listError, setListError] = useState('');
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState('');
  const [reviewingDraft, setReviewingDraft] = useState(null);
  const fileInputRef = useRef(null);

  function load() {
    if (!storeId) return;
    api.getMaterials(storeId).then(setMaterials);
    api.getReceivings(storeId).then(setReceivings);
    api.listDrafts(storeId).then(setDrafts);
  }
  useEffect(load, [storeId]);

  async function removeReceiving(r) {
    // Spelled out because this is not only a line disappearing from a
    // list: the ingredients come back off the shelf and the price comes
    // back out of the average cost.
    const ok = window.confirm(
      `ลบรายการซื้อของจาก "${r.supplier || 'ไม่ระบุผู้ขาย'}" ฿${(r.total || 0).toLocaleString()}?\n\n` +
      'วัตถุดิบที่รับเข้ามาจะถูกหักออกจากสต๊อก และราคาจะถูกถอดออกจากต้นทุนเฉลี่ยด้วย');
    if (!ok) return;
    setBusyId(r.id);
    setListError('');
    try {
      await api.deleteReceiving(storeId, r.id);
      load();
    } catch (e) {
      setListError(e.message);
    } finally {
      setBusyId('');
    }
  }

  if (!storeId) return <p>เลือกสาขาในหน้าตั้งค่าก่อน</p>;

  const materialName = (id) => materials.find((m) => m.id === id)?.name || id;
  const materialUnit = (id) => materials.find((m) => m.id === id)?.unit || '';

  async function handleFileSelected(e) {
    const file = e.target.files[0];
    e.target.value = ''; // allow picking the same file again later
    if (!file) return;
    setScanning(true);
    setScanError('');
    try {
      const compressed = await compressImage(file);
      const draft = await api.scanInvoice(storeId, compressed);
      setDrafts((prev) => [draft, ...prev]);
      setReviewingDraft(draft);
    } catch (err) {
      setScanError(`อ่านใบส่งของไม่สำเร็จ: ${err.message}`);
    } finally {
      setScanning(false);
    }
  }

  return (
    <div>
      <p style={{ fontSize: 15, fontWeight: 500, margin: '0 0 4px' }}>ซื้อของเข้าร้าน</p>
      <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '0 0 14px' }}>
        ถ่ายรูปใบส่งของแล้ว AI จะอ่านให้ - ตรวจก่อนกดยืนยัน สต๊อกและต้นทุนถึงจะอัปเดต
      </p>

      {/* The scan button leads, because people open this page to record a
          delivery, not to browse past ones. */}
      <input type="file" accept="image/*" ref={fileInputRef} style={{ display: 'none' }}
        onChange={handleFileSelected} />
      <button onClick={() => fileInputRef.current?.click()} disabled={scanning}
        style={{
          width: '100%', padding: 15, fontSize: 15, fontWeight: 600,
          borderRadius: 12, background: 'var(--accent)', color: '#fff',
        }}>
        {scanning ? 'กำลังอ่าน...' : '📷 บันทึกด้วยรูปถ่าย'}
      </button>
      <button onClick={() => setShowForm(true)}
        style={{ width: '100%', padding: 12, marginTop: 8, fontSize: 13 }}>
        ✏️ บันทึกใบส่งของ
      </button>
      <div style={{ height: 16 }} />
      {scanError && <p style={{ fontSize: 12, color: 'var(--text-danger)', marginBottom: 12 }}>{scanError}</p>}

      {drafts.length > 0 && (
        <div className="card" style={{ marginBottom: 16, borderColor: 'var(--text-warning)' }}>
          <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 4px' }}>
            ⚠ รอตรวจก่อนเข้าสต๊อก ({drafts.length})
          </p>
          <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 12px' }}>
            ยังไม่เข้าสต๊อกจนกว่าจะตรวจและกดยืนยัน
          </p>
          {drafts.map((d, idx) => (
            <div key={d.id} style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0',
              borderBottom: idx < drafts.length - 1 ? '0.5px solid var(--border)' : 'none',
            }}>
              <span style={{ fontSize: 13 }}>{d.supplier || 'ไม่ระบุผู้ขาย'} · {(d.items || []).length} รายการ</span>
              <button onClick={() => setReviewingDraft(d)} style={{ fontSize: 12, padding: '6px 8px' }}>ตรวจสอบ</button>
            </div>
          ))}
        </div>
      )}

      <div className="card">
        {receivings.length === 0 && (
          <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>ยังไม่มีประวัติการซื้อของ</p>
        )}
        {listError && (
          <p style={{ fontSize: 12, color: 'var(--text-danger)', margin: '0 0 10px' }}>{listError}</p>
        )}
        {receivings.map((r, idx) => (
          <div key={r.id} style={{
            padding: '12px 0',
            borderBottom: idx < receivings.length - 1 ? '0.5px solid var(--border)' : 'none',
          }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4,
            }}>
              <span style={{ fontSize: 14, flex: 1, minWidth: 0 }}>
                {r.supplier || 'ไม่ระบุผู้ขาย'}
              </span>
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{r.date}</span>
              <span style={{ fontSize: 14 }}>฿{(r.total || 0).toLocaleString()}</span>
              <span style={{ display: 'flex', gap: 4, flex: 'none' }}>
                <button onClick={() => setEditingReceiving(r)} disabled={busyId === r.id}
                  style={{ fontSize: 11, padding: '4px 8px' }}>แก้ไข</button>
                <button onClick={() => removeReceiving(r)} disabled={busyId === r.id}
                  style={{ fontSize: 11, padding: '4px 8px', color: 'var(--text-danger)' }}>
                  {busyId === r.id ? '...' : 'ลบ'}
                </button>
              </span>
            </div>
            {(r.items || []).map((it, i) => (
              <div key={i} style={{ fontSize: 12, color: 'var(--text-secondary)', paddingLeft: 8 }}>
                {materialName(it.material_id)} {it.quantity} {materialUnit(it.material_id)} × ฿{it.unit_cost}
              </div>
            ))}
          </div>
        ))}
      </div>

      {(showForm || editingReceiving) && (
        <ReceivingForm materials={materials} onMaterialsChanged={load}
          receiving={editingReceiving}
          onCancel={() => { setShowForm(false); setEditingReceiving(null); }}
          onSaved={() => { setShowForm(false); setEditingReceiving(null); load(); }}
          storeId={storeId} />
      )}

      {reviewingDraft && (
        <DraftReview
          draft={reviewingDraft}
          materials={materials}
          storeId={storeId}
          onClose={() => setReviewingDraft(null)}
          onDone={() => { setReviewingDraft(null); load(); }}
        />
      )}
    </div>
  );
}

function ImagePreview({ storeId, draftId }) {
  const [url, setUrl] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let objectUrl = null;
    let cancelled = false;

    api.getDraftImageUrl(storeId, draftId)
      .then((u) => {
        if (cancelled) { URL.revokeObjectURL(u); return; }
        objectUrl = u;
        setUrl(u);
      })
      // Show what actually went wrong. Guessing "probably expired" was
      // worse than saying nothing: the draft is minutes old, so that
      // explanation sent the reader looking in the wrong place.
      .catch((e) => !cancelled && setError(e.message));

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [storeId, draftId]);

  if (error) {
    return (
      <p style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 12 }}>
        (โหลดรูปไม่สำเร็จ: {error} - ใช้ข้อมูลที่ AI อ่านได้ด้านล่างแทน)
      </p>
    );
  }
  if (!url) {
    return (
      <p style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 12 }}>
        กำลังโหลดรูป...
      </p>
    );
  }

  return (
    <div style={{ marginBottom: 12 }}>
      <img src={url} alt="ใบส่งของที่สแกน"
        style={{ width: '100%', borderRadius: 8, border: '1px solid var(--border)', display: 'block' }} />
      <p style={{ fontSize: 10, color: 'var(--text-muted)', margin: '4px 0 0' }}>
        รูปนี้จะถูกลบอัตโนมัติภายใน 7 วัน
      </p>
    </div>
  );
}

function DraftReview({ draft, materials, storeId, onClose, onDone }) {
  const [items, setItems] = useState(draft.items || []);
  const [localMaterials, setLocalMaterials] = useState(materials);
  const [supplier, setSupplier] = useState(draft.supplier || '');
  const [date, setDate] = useState((draft.date || '').slice(0, 10) || new Date().toISOString().slice(0, 10));
  const [confirming, setConfirming] = useState(false);
  const [discarding, setDiscarding] = useState(false);
  const [error, setError] = useState('');
  const [creatingFor, setCreatingFor] = useState(null); // index of item getting a new material

  useEffect(() => setLocalMaterials(materials), [materials]);

  function updateItem(idx, patch) {
    setItems(items.map((it, i) => (i === idx ? { ...it, ...patch } : it)));
  }

  async function pickMaterial(idx, materialId) {
    const mat = localMaterials.find((m) => m.id === materialId);
    const match = { matched: true, material_id: materialId, material_name: mat?.name, via: 'manual' };
    // re-run unit conversion against the newly picked material's unit
    try {
      const converted = await api.convertUnit(storeId, items[idx], materialId);
      updateItem(idx, { ...converted, match });
    } catch {
      updateItem(idx, { match }); // conversion is a nice-to-have, don't block picking on its failure
    }
  }

  function removeItem(idx) {
    setItems(items.filter((_, i) => i !== idx));
  }

  const unmatchedCount = items.filter((it) => !it.match?.material_id).length;
  const lowConfidenceCount = items.filter((it) => typeof it.confidence === 'number' && it.confidence < 0.7).length;
  const unitIssueCount = items.filter((it) => {
    const s = it.unit_conversion?.status;
    return s === 'unconvertible' || s === 'unrecognized';
  }).length;
  const missingPriceCount = items.filter((it) => it.price_source === 'missing').length;

  async function saveEditsThenConfirm() {
    setConfirming(true);
    setError('');
    try {
      await api.updateDraft(storeId, draft.id, { supplier, date, items });
      const result = await api.confirmDraft(storeId, draft.id);
      if (result.skipped_items?.length) {
        setError(`บันทึกแล้ว แต่ข้าม ${result.skipped_items.length} รายการที่ยังไม่ได้เลือกวัตถุดิบ: ${result.skipped_items.join(', ')}`);
        setTimeout(onDone, 2500);
      } else {
        onDone();
      }
    } catch (e) {
      setError(`Confirm ไม่สำเร็จ: ${e.message}`);
    } finally {
      setConfirming(false);
    }
  }

  async function discard() {
    setDiscarding(true);
    try {
      await api.discardDraft(storeId, draft.id);
      onDone();
    } catch (e) {
      setError(`ลบไม่สำเร็จ: ${e.message}`);
      setDiscarding(false);
    }
  }

  function priceNote(it) {
    if (it.price_source === 'history') {
      return <p style={{ fontSize: 11, color: 'var(--text-warning)', margin: '2px 0 0' }}>
        ⚠ AI อ่านราคาไม่ได้ - ใช้ราคาเฉลี่ยที่เคยซื้อล่าสุดแทน ตรวจสอบก่อน Confirm
      </p>;
    }
    if (it.price_source === 'missing') {
      return <p style={{ fontSize: 11, color: 'var(--text-danger)', margin: '2px 0 0' }}>
        ⚠ ไม่มีราคา และไม่เคยซื้อวัตถุดิบนี้มาก่อน - ต้องกรอกราคาเองก่อน Confirm
      </p>;
    }
    return null;
  }

  function unitNote(it) {
    const uc = it.unit_conversion;
    if (!uc) return null;
    if (uc.status === 'converted') {
      return <p style={{ fontSize: 11, color: 'var(--text-secondary)', margin: '2px 0 0' }}>
        แปลงจาก {uc.original_qty} {uc.original_unit} เป็น {it.qty} {uc.target_unit} อัตโนมัติ
      </p>;
    }
    if (uc.status === 'unconvertible' || uc.status === 'unrecognized') {
      return <p style={{ fontSize: 11, color: 'var(--text-warning)', margin: '2px 0 0' }}>
        ⚠ หน่วยไม่ตรงกับคลัง ({it.unit} ≠ {uc.target_unit}) - เช็คจำนวนก่อน Confirm
      </p>;
    }
    return null;
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" style={{ width: 500, maxWidth: '92vw', maxHeight: '85vh', overflowY: 'auto' }}
        onClick={(e) => e.stopPropagation()}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 4px' }}>ตรวจสอบใบรับของ (จาก AI)</p>
        <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 16px' }}>
          ยังไม่มีอะไรเข้าสต๊อก - ตรวจแล้วกด Confirm ด้านล่างเมื่อพร้อม
        </p>

        {draft.warning && (
          <div style={{
            background: 'var(--surface-1)', borderLeft: '3px solid var(--text-danger)',
            borderRadius: 6, padding: '10px 12px', marginBottom: 12,
            fontSize: 12, color: 'var(--text-danger)',
          }}>
            ⚠ {draft.warning}
          </div>
        )}

        {draft.image_path ? (
          <ImagePreview storeId={storeId} draftId={draft.id} />
        ) : (
          <p style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 12 }}>
            (ไม่มีรูปเก็บไว้ให้ดูเทียบ - ใช้ข้อมูลที่ AI อ่านได้ด้านล่างแทน)
          </p>
        )}

        <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
          <div style={{ flex: 1 }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>ผู้ขาย</label>
            <input value={supplier} onChange={(e) => setSupplier(e.target.value)} style={{ width: '100%', marginTop: 4 }} />
          </div>
          <div>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>วันที่</label>
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} style={{ width: '100%', marginTop: 4 }} />
          </div>
        </div>

        {(unmatchedCount > 0 || lowConfidenceCount > 0 || unitIssueCount > 0 || missingPriceCount > 0) && (
          <div style={{
            background: '#fdf3e3', border: '1px solid var(--text-warning)', borderRadius: 8,
            padding: '8px 12px', marginBottom: 12, fontSize: 12, color: 'var(--text-warning)',
          }}>
            {unmatchedCount > 0 && <div>{unmatchedCount} รายการยังไม่ได้เลือกวัตถุดิบ</div>}
            {lowConfidenceCount > 0 && <div>{lowConfidenceCount} รายการ AI มั่นใจต่ำ - ตรวจตัวเลขอีกรอบ</div>}
            {unitIssueCount > 0 && <div>{unitIssueCount} รายการหน่วยไม่ตรงกับคลัง - เช็คจำนวนเอง</div>}
            {missingPriceCount > 0 && <div style={{ color: 'var(--text-danger)' }}>{missingPriceCount} รายการไม่มีราคา - ต้องกรอกก่อน Confirm</div>}
          </div>
        )}

        <div className="receiving-items-grid receiving-items-header">
          <span>รายการ</span>
          <span>จำนวน</span>
          <span>ราคาต่อหน่วย</span>
          <span aria-hidden="true" />
        </div>

        {items.map((it, idx) => {
          const conf = it.confidence;
          const lowConf = typeof conf === 'number' && conf < 0.7;
          const matched = !!it.match?.material_id;
          const suggestions = it.match?.suggestions || [];
          return (
            <div key={idx} style={{
              padding: '10px 0', borderBottom: idx < items.length - 1 ? '0.5px solid var(--border)' : 'none',
              background: lowConf ? '#fffbf0' : undefined,
            }}>
              <div className="receiving-items-grid" style={{ alignItems: 'center', marginBottom: 4 }}>
                <span style={{ fontSize: 12.5, minWidth: 0 }}>
                  {it.name}
                  {lowConf && (
                    <span style={{ color: 'var(--text-warning)', display: 'block', fontSize: 10.5 }}>
                      AI มั่นใจ {Math.round(conf * 100)}%
                    </span>
                  )}
                </span>
                <div style={{ position: 'relative', minWidth: 0 }}>
                  <input type="number" value={it.qty}
                    onChange={(e) => updateItem(idx, { qty: parseFloat(e.target.value) || 0 })}
                    style={{ width: '100%', fontSize: 12, paddingRight: it.unit ? 32 : 8 }} placeholder="0" />
                  {it.unit && (
                    <span style={{
                      position: 'absolute', right: 7, top: '50%', transform: 'translateY(-50%)',
                      fontSize: 9.5, color: 'var(--text-muted)', pointerEvents: 'none',
                    }}>
                      {it.unit}
                    </span>
                  )}
                </div>
                <input type="number" value={it.price ?? ''} onChange={(e) => updateItem(idx, { price: e.target.value === '' ? null : parseFloat(e.target.value) || 0, price_source: 'edited' })}
                  style={{ width: '100%', minWidth: 0, fontSize: 12 }} placeholder="0.00" />
                <button onClick={() => removeItem(idx)} aria-label={`ลบรายการที่ ${idx + 1}`}
                  style={{ fontSize: 14, padding: '4px 5px', color: 'var(--text-muted)' }}>×</button>
              </div>
              {priceNote(it)}
              {unitNote(it)}

              {matched ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
                  <span style={{ fontSize: 12, color: 'var(--text-success)' }}>
                    ✓ จับคู่: {it.match.material_name}
                  </span>
                  {/* Searchable, because a matched row often needs
                      correcting and scrolling a long list to do it is
                      how a wrong match gets left alone. */}
                  <div style={{ marginLeft: 'auto', width: 150 }}>
                    <MaterialPicker materials={localMaterials} value={it.match.material_id}
                      storeId={storeId} onChange={(id) => pickMaterial(idx, id)}
                      onCreated={async () => setLocalMaterials(await api.getMaterials(storeId))} />
                  </div>
                </div>
              ) : creatingFor === idx ? (
                <QuickCreateMaterial
                  storeId={storeId}
                  defaultName={it.name}
                  defaultUnit={it.unit}
                  onCancel={() => setCreatingFor(null)}
                  onCreated={(mat) => {
                    setLocalMaterials((prev) => [...prev, mat]);
                    setCreatingFor(null);
                    pickMaterial(idx, mat.id);
                  }}
                />
              ) : (
                <div style={{ marginTop: 4 }}>
                  <p style={{ fontSize: 12, color: 'var(--text-danger)', margin: '0 0 4px' }}>
                    ยังไม่พบวัตถุดิบที่ตรงกัน
                  </p>
                  <select defaultValue="" onChange={(e) => e.target.value && pickMaterial(idx, e.target.value)}
                    style={{ width: '100%', fontSize: 12, marginBottom: 4 }}>
                    <option value="" disabled>
                      {suggestions.length ? 'เลือกจากที่ใกล้เคียง หรือดูรายการทั้งหมด' : 'เลือกวัตถุดิบที่มีอยู่'}
                    </option>
                    {suggestions.map((s) => (
                      <option key={s.material_id} value={s.material_id}>
                        {s.name} (ใกล้เคียง {Math.round(s.score * 100)}%)
                      </option>
                    ))}
                    {localMaterials.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                  </select>
                  <button onClick={() => setCreatingFor(idx)} style={{ fontSize: 11, width: '100%' }}>
                    + สร้างวัตถุดิบใหม่ "{it.name}"
                  </button>
                </div>
              )}
            </div>
          );
        })}

        {error && <p style={{ fontSize: 12, color: 'var(--text-danger)', marginTop: 12 }}>{error}</p>}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'space-between', marginTop: 16 }}>
          <button onClick={discard} disabled={discarding || confirming}>
            {discarding ? 'กำลังลบ...' : 'ทิ้งร่างนี้'}
          </button>
          <div style={{ display: 'flex', gap: 8 }}>
            <button onClick={onClose}>ปิด</button>
            <button style={{ background: 'var(--surface-1)' }} onClick={saveEditsThenConfirm}
              disabled={confirming || discarding}>
              {confirming ? 'กำลังยืนยัน...' : 'Confirm เข้าสต๊อก'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

const QUICK_UNITS = ['กรัม', 'กก.', 'มล.', 'ลิตร', 'ชิ้น', 'ขวด'];

function QuickCreateMaterial({ storeId, defaultName, defaultUnit, onCancel, onCreated }) {
  const [name, setName] = useState(defaultName || '');
  const [unit, setUnit] = useState(QUICK_UNITS.includes(defaultUnit) ? defaultUnit : QUICK_UNITS[0]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  async function create() {
    if (!name.trim()) {
      setError('ใส่ชื่อวัตถุดิบก่อน');
      return;
    }
    setSaving(true);
    setError('');
    const id = newId();
    try {
      await api.upsertMaterial(storeId, id, { name: name.trim(), unit, cost: 0, par: 0 });
      onCreated({ id, name: name.trim(), unit, cost: 0, par: 0, stock: 0 });
    } catch (e) {
      setError(`สร้างไม่สำเร็จ: ${e.message}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={{ background: 'var(--surface-1)', borderRadius: 8, padding: 8, marginTop: 4 }}>
      <p style={{ fontSize: 12, fontWeight: 500, margin: '0 0 6px' }}>สร้างวัตถุดิบใหม่</p>
      <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="ชื่อวัตถุดิบ"
          style={{ flex: 1, fontSize: 12 }} />
        <select value={unit} onChange={(e) => setUnit(e.target.value)} style={{ fontSize: 12 }}>
          {QUICK_UNITS.map((u) => <option key={u}>{u}</option>)}
        </select>
      </div>
      {error && <p style={{ fontSize: 11, color: 'var(--text-danger)', margin: '0 0 6px' }}>{error}</p>}
      <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
        <button onClick={onCancel} style={{ fontSize: 11 }}>ยกเลิก</button>
        <button onClick={create} disabled={saving} style={{ fontSize: 11, background: 'var(--surface-2)' }}>
          {saving ? 'กำลังสร้าง...' : 'สร้างและเลือก'}
        </button>
      </div>
    </div>
  );
}

function ReceivingForm({ materials, onCancel, onSaved, storeId, onMaterialsChanged,
                        receiving }) {
  // The same form for recording and for correcting: correcting is not a
  // different job, it is the same one done again with the right numbers.
  const isEdit = !!receiving?.id;
  const [supplier, setSupplier] = useState(receiving?.supplier || '');
  const [date, setDate] = useState(receiving?.date || new Date().toISOString().slice(0, 10));
  const [rows, setRows] = useState(() => (receiving?.items || []).map((i) => ({
    material_id: i.material_id, quantity: i.quantity, unit_cost: i.unit_cost,
  })));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  function addRow() {
    // Deliberately blank rather than defaulting to the first material:
    // a pre-filled pick that nobody chose is indistinguishable from one
    // they did, and it lands in stock either way.
    setRows([...rows, { material_id: '', quantity: 0, unit_cost: 0 }]);
  }
  function updateRow(idx, patch) {
    setRows(rows.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  }
  function removeRow(idx) {
    setRows(rows.filter((_, i) => i !== idx));
  }

  const total = rows.reduce((s, r) => s + (r.quantity || 0) * (r.unit_cost || 0), 0);
  const unitOf = (id) => materials.find((m) => m.id === id)?.unit || '';

  async function save() {
    const valid = rows.filter((r) => r.material_id && r.quantity > 0);
    if (valid.length === 0) {
      setError('ใส่รายการอย่างน้อย 1 รายการ เลือกวัตถุดิบและใส่จำนวนมากกว่า 0');
      return;
    }
    // Rows that are half-filled would otherwise be dropped without a word,
    // and the saved total wouldn't match the delivery note in hand.
    const incomplete = rows.length - valid.length;
    if (incomplete > 0) {
      setError(`มี ${incomplete} รายการที่ยังไม่ครบ (ต้องเลือกวัตถุดิบและใส่จำนวน) - ลบออกหรือกรอกให้ครบก่อน`);
      return;
    }
    setSaving(true);
    setError('');
    try {
      if (isEdit) await api.updateReceiving(storeId, receiving.id, { supplier, date, items: valid });
      else await api.addReceiving(storeId, { supplier, date, items: valid });
      onSaved();
    } catch (e) {
      setError(`บันทึกไม่สำเร็จ: ${e.message}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-box" style={{ width: 460, maxWidth: '92vw', maxHeight: '80vh', overflowY: 'auto' }}
        onClick={(e) => e.stopPropagation()}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 6px' }}>
          {isEdit ? 'แก้ไขรายการซื้อของ' : 'บันทึกใบส่งของ'}
        </p>
        {isEdit && (
          <p style={{ fontSize: 11.5, color: 'var(--text-muted)', margin: '0 0 14px' }}>
            บันทึกแล้วระบบจะปรับสต๊อกและต้นทุนเฉลี่ยตามตัวเลขใหม่ให้เอง
          </p>
        )}
        {!isEdit && <div style={{ height: 10 }} />}

        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>ผู้ขาย / ซัพพลายเออร์</label>
        <input value={supplier} onChange={(e) => setSupplier(e.target.value)}
          placeholder="เช่น Makro" style={{ width: '100%', margin: '4px 0 12px' }} />

        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>วันที่รับของ</label>
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)}
          style={{ width: '100%', margin: '4px 0 12px' }} />

        <div className="receiving-items-grid receiving-items-header">
          <span>รายการ</span>
          <span>จำนวน</span>
          <span>ราคาต่อหน่วย</span>
          <span aria-hidden="true" />
        </div>
        {rows.length === 0 && (
          <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '8px 0 4px' }}>
            ยังไม่มีรายการ กด "เพิ่มรายการ" ด้านล่าง
          </p>
        )}
        {rows.map((r, idx) => (
          <div key={idx} className="receiving-items-grid" style={{ alignItems: 'center', marginBottom: 8 }}>
            <MaterialPicker materials={materials} value={r.material_id} storeId={storeId}
              onChange={(id) => updateRow(idx, { material_id: id })}
              onCreated={onMaterialsChanged} />
            <div style={{ position: 'relative', minWidth: 0 }}>
              <input type="number" value={r.quantity} placeholder="0"
                onChange={(e) => updateRow(idx, { quantity: parseFloat(e.target.value) || 0 })}
                style={{ width: '100%', fontSize: 12, paddingRight: unitOf(r.material_id) ? 32 : 8 }} />
              {unitOf(r.material_id) && (
                <span style={{
                  position: 'absolute', right: 7, top: '50%', transform: 'translateY(-50%)',
                  fontSize: 9.5, color: 'var(--text-muted)', pointerEvents: 'none',
                }}>
                  {unitOf(r.material_id)}
                </span>
              )}
            </div>
            <input type="number" value={r.unit_cost} placeholder="0.00"
              onChange={(e) => updateRow(idx, { unit_cost: parseFloat(e.target.value) || 0 })}
              style={{ width: '100%', minWidth: 0, fontSize: 12 }} />
            <button onClick={() => removeRow(idx)} aria-label={`ลบรายการที่ ${idx + 1}`}
              style={{ padding: '4px 5px', color: 'var(--text-muted)' }}>×</button>
          </div>
        ))}
        <button onClick={addRow} style={{ width: '100%', marginTop: 4 }}>+ เพิ่มรายการ</button>

        <div style={{
          display: 'flex', justifyContent: 'space-between', fontSize: 14, fontWeight: 500,
          marginTop: 12, paddingTop: 12, borderTop: '0.5px solid var(--border)',
        }}>
          <span>รวม</span>
          <span>฿{total.toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
        </div>

        {error && <p style={{ fontSize: 12, color: 'var(--text-danger)', marginTop: 8 }}>{error}</p>}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
          <button onClick={onCancel}>ยกเลิก</button>
          <button style={{ background: 'var(--surface-1)' }} onClick={save} disabled={saving}>
            {saving ? 'กำลังบันทึก...' : 'บันทึก'}
          </button>
        </div>
      </div>
    </div>
  );
}
