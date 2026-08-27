import { useEffect, useState } from 'react';
import { useStore } from '../store/StoreContext';
import { useAuth } from '../auth/AuthContext';
import { api } from '../api/client';

export default function Settings() {
  const { stores, storeId, selectStore, loading, refreshStores, clearStores } = useStore();
  const { reloadProfile } = useAuth();
  const [appSettings, setAppSettings] = useState(null);
  const [nameInput, setNameInput] = useState('');
  const [savingName, setSavingName] = useState(false);
  const [tokenInput, setTokenInput] = useState('');
  const [labelInput, setLabelInput] = useState('');
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState('');
  const [connections, setConnections] = useState([]);
  const [removingId, setRemovingId] = useState('');
  const [showAdd, setShowAdd] = useState(false);
  const [intervalInput, setIntervalInput] = useState('');
  const [savingInterval, setSavingInterval] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [showTools, setShowTools] = useState(false);
  const [syncResult, setSyncResult] = useState(null);
  const [syncError, setSyncError] = useState('');
  const [resettingCursor, setResettingCursor] = useState(false);
  const [migrating, setMigrating] = useState(false);
  const [migrateResult, setMigrateResult] = useState(null);
  const [rebuilding, setRebuilding] = useState(false);
  const [rebuildResult, setRebuildResult] = useState(null);

  async function runMigration() {
    if (!storeId) return;
    setMigrating(true);
    try {
      const res = await api.migrateStock(storeId);
      setMigrateResult(res);
    } catch (e) {
      setConnectError(`ย้ายข้อมูลไม่สำเร็จ: ${e.message}`);
    } finally {
      setMigrating(false);
    }
  }

  async function runRebuild() {
    if (!storeId) return;
    setRebuilding(true);
    setRebuildResult(null);
    try {
      setRebuildResult(await api.rebuildStockSnapshot(storeId));
    } catch (e) {
      setConnectError(`คำนวณใหม่ไม่สำเร็จ: ${e.message}`);
    } finally {
      setRebuilding(false);
    }
  }

  function loadAppSettings() {
    api.getAppSettings().then((s) => {
      setAppSettings(s);
      setIntervalInput(String(s.sync_interval_seconds));
      setNameInput(s.business_name || '');
    });
  }
  useEffect(() => {
    loadAppSettings();
    loadConnections();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function saveBusinessName() {
    if (!nameInput.trim()) return;
    setSavingName(true);
    try {
      await api.saveBusinessName(nameInput.trim());
      loadAppSettings();
      await reloadProfile();   // the sidebar shows this name too
    } catch (e) {
      setConnectError(e.message);
    } finally {
      setSavingName(false);
    }
  }

  function loadConnections() {
    api.listConnections()
      .then((r) => setConnections(r.connections || []))
      .catch(() => setConnections([]));
  }

  async function addConnection() {
    if (!tokenInput.trim()) return;
    setConnecting(true);
    setConnectError('');
    try {
      await api.addConnection(tokenInput.trim(), labelInput.trim());
      setTokenInput('');
      setLabelInput('');
      setShowAdd(false);
      loadConnections();
      loadAppSettings();
      await refreshStores(); // no full page reload - avoids the SPA-route 404
    } catch (e) {
      setConnectError(e.message);
    } finally {
      setConnecting(false);
    }
  }

  async function removeConnection(conn) {
    // Said out loud, because "นำออก" next to a shop name reads like it
    // might take the shop's history with it. It doesn't, and the moment
    // to say so is before the click, not in a help page.
    const branches = (conn.stores || []).map((st) => st.name).join(', ');
    const ok = window.confirm(
      `หยุดซิงก์บัญชี "${conn.label}"${branches ? ` (${branches})` : ''}?\n\n` +
      'ข้อมูลที่ซิงก์ไว้แล้ว — ยอดขาย สต๊อก สูตรอาหาร — ยังอยู่ครบ ไม่ได้ถูกลบ');
    if (!ok) return;

    setRemovingId(conn.id);
    setConnectError('');
    try {
      await api.removeConnection(conn.id);
      loadConnections();
      loadAppSettings();
      const remaining = await refreshStores();
      // The branch being viewed may have just gone away with its account.
      if (!remaining.some((st) => st.id === storeId)) clearStores();
    } catch (e) {
      setConnectError(e.message);
    } finally {
      setRemovingId('');
    }
  }

  async function saveInterval() {
    setSavingInterval(true);
    await api.saveSyncInterval(parseInt(intervalInput) || 300);
    loadAppSettings();
    setSavingInterval(false);
  }

  async function runSync() {
    if (!storeId) return;
    setSyncing(true);
    setSyncError('');
    try {
      const res = await api.sync(storeId);
      setSyncResult(res);
    } catch (e) {
      setSyncError(e.message);
    } finally {
      setSyncing(false);
    }
  }

  async function resetCursor() {
    if (!storeId) return;
    if (!window.confirm(
      'รีเซ็ตจุดซิงก์เป็น "ตอนนี้"? บิลที่ขายไปก่อนหน้านี้ที่ยังไม่ถูกดึงเข้ามาจะถูกข้ามไปเลย ไม่ย้อนไปดึงย้อนหลัง')) return;
    setResettingCursor(true);
    setSyncError('');
    try {
      await api.resetSyncCursor(storeId);
      setSyncResult(null);
    } catch (e) {
      setSyncError(e.message);
    } finally {
      setResettingCursor(false);
    }
  }

  return (
    <div>
      <p style={{ fontSize: 15, fontWeight: 500, margin: '0 0 16px' }}>ตั้งค่า</p>

      <div className="card" style={{ marginBottom: 16 }}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 4px' }}>ข้อมูลธุรกิจ</p>
        <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 12px' }}>
          ข้อมูลทั้งหมดของธุรกิจนี้แยกจากธุรกิจอื่นในระบบโดยสิ้นเชิง
        </p>
        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>ชื่อธุรกิจ</label>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '4px 0 12px' }}>
          <input value={nameInput} onChange={(e) => setNameInput(e.target.value)}
            style={{ flex: 1, minWidth: 0 }} />
          <button onClick={saveBusinessName} disabled={savingName}>
            {savingName ? 'กำลังบันทึก...' : 'บันทึก'}
          </button>
        </div>
        {appSettings && (
          <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: 0 }}>
            สมาชิก {appSettings.user_count} คน · {stores.length} สาขา
            {appSettings.created_at && ` · สร้างเมื่อ ${formatDate(appSettings.created_at)}`}
          </p>
        )}
      </div>

      {/* One Loyverse access token is one Loyverse ACCOUNT, and an account
          is not the same thing as a business. Shops that grew branch by
          branch usually opened a separate account for each, so a single
          business can hold several tokens - which the old single-token
          screen had no way to express: the owner connected one branch and
          the rest were simply unreachable. */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div style={{
          display: 'flex', alignItems: 'baseline',
          justifyContent: 'space-between', gap: 12, marginBottom: 4,
        }}>
          <p style={{ fontSize: 14, fontWeight: 500, margin: 0 }}>บัญชี Loyverse</p>
          {connections.length > 0 && !showAdd && (
            <button onClick={() => setShowAdd(true)} style={{ fontSize: 12 }}>
              + เพิ่มบัญชี
            </button>
          )}
        </div>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '0 0 12px' }}>
          ถ้าแต่ละสาขาใช้บัญชี Loyverse แยกกัน ให้เพิ่ม token ของทุกบัญชีที่นี่
          แล้วสลับสาขาได้จากหน้าแรก — ข้อมูลของแต่ละสาขาแยกกันคนละชุด ไม่ปนกัน
        </p>

        {connections.map((conn) => (
          <div key={conn.id} style={{
            border: '1px solid var(--border)', borderRadius: 9,
            padding: '11px 13px', marginBottom: 8,
          }}>
            <div style={{
              display: 'flex', alignItems: 'center',
              justifyContent: 'space-between', gap: 10,
            }}>
              <span style={{ fontSize: 13, fontWeight: 500, minWidth: 0 }}>
                {conn.label}
              </span>
              <button onClick={() => removeConnection(conn)}
                disabled={removingId === conn.id}
                style={{ fontSize: 11.5, flex: 'none' }}>
                {removingId === conn.id ? 'กำลังนำออก...' : 'นำออก'}
              </button>
            </div>
            <p style={{ fontSize: 11.5, color: 'var(--text-muted)', margin: '4px 0 0' }}>
              {(conn.stores || []).length > 0
                ? (conn.stores || []).map((st) => st.name).join(' · ')
                : 'ยังไม่พบสาขาในบัญชีนี้'}
            </p>
            {/* A dead token stops this account and nothing else, so it is
                reported on the account rather than as a page-wide error. */}
            {conn.error && (
              <p style={{ fontSize: 11.5, color: 'var(--text-danger)', margin: '6px 0 0' }}>
                เชื่อมต่อบัญชีนี้ไม่ได้ — {conn.error}
                <br />สาขาของบัญชีอื่นยังใช้งานได้ตามปกติ
              </p>
            )}
          </div>
        ))}

        {(connections.length === 0 || showAdd) && (
          <div style={{ marginTop: connections.length ? 12 : 0 }}>
            <div style={{
              background: 'var(--surface-1)', borderRadius: 8, padding: '10px 14px',
              marginBottom: 12, fontSize: 12, color: 'var(--text-secondary)',
            }}>
              <p style={{ margin: '0 0 6px', fontWeight: 500 }}>วิธีสร้าง Access Token:</p>
              <p style={{ margin: '0 0 2px' }}>1. เข้า Loyverse Back Office ของบัญชีที่ต้องการ</p>
              <p style={{ margin: '0 0 2px' }}>2. ไปที่ Settings → Access Tokens</p>
              <p style={{ margin: 0 }}>3. กด "เพิ่ม Access Token" แล้วคัดลอกมาวางด้านล่าง</p>
            </div>
            <button onClick={() => window.open('https://r.loyverse.com/dashboard/#/settings/access-tokens', '_blank')}
              style={{ marginBottom: 12 }}>
              ไปที่ Loyverse Back Office
            </button>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Access token</label>
            <input type="password" value={tokenInput} onChange={(e) => setTokenInput(e.target.value)}
              placeholder="วาง Loyverse access token ที่นี่"
              style={{ width: '100%', margin: '4px 0 10px' }} />
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              ชื่อเรียกบัญชีนี้ (ไม่ใส่ก็ได้)
            </label>
            <input value={labelInput} onChange={(e) => setLabelInput(e.target.value)}
              placeholder="เช่น สาขาสีลม"
              style={{ width: '100%', margin: '4px 0 12px' }} />
            <div style={{ display: 'flex', gap: 8 }}>
              <button onClick={addConnection} disabled={connecting}>
                {connecting ? 'กำลังเชื่อมต่อ...' : 'เชื่อมต่อ'}
              </button>
              {connections.length > 0 && (
                <button onClick={() => { setShowAdd(false); setConnectError(''); }}>
                  ยกเลิก
                </button>
              )}
            </div>
          </div>
        )}

        {connectError && (
          <p style={{ fontSize: 12, color: 'var(--text-danger)', marginTop: 8 }}>{connectError}</p>
        )}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 4px' }}>สาขาที่ใช้งาน</p>
        <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 12px' }}>
          ข้อมูลสต๊อก สูตร และรายรับรายจ่ายของแต่ละสาขาแยกจากกันทั้งหมด
          — สลับสาขาได้จากหน้าแรกเช่นกัน
        </p>
        {loading && <p style={{ fontSize: 13 }}>กำลังโหลดรายชื่อสาขา...</p>}
        {!loading && stores.length === 0 && (
          <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>
            เชื่อมต่อ Loyverse ก่อนถึงจะเห็นรายชื่อสาขา
          </p>
        )}
        {stores.map((s) => (
          <label key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0', fontSize: 13 }}>
            <input type="radio" checked={storeId === s.id} onChange={() => selectStore(s.id)} />
            <span style={{ minWidth: 0 }}>
              {s.name}
              {/* Only when the branches span more than one Loyverse
                  account - two accounts can each hold a "สาขา 1". */}
              {s.show_account && s.connection_label && (
                <span style={{ color: 'var(--text-muted)' }}> · {s.connection_label}</span>
              )}
            </span>
            {storeId === s.id && <span style={{ fontSize: 11, color: 'var(--text-success)' }}>กำลังใช้งาน</span>}
          </label>
        ))}
      </div>

      {/* Everything below this line is maintenance: a one-time migration,
          a recompute, a manual sync, a cursor reset. None of it means
          anything to a shop that set up today, and it was sitting at the
          same weight as the one field they actually need - with
          "รีเซ็ตจุดซิงก์" among it, which is exactly the button someone
          presses when they are confused and looking for something to try.

          Folded away rather than removed: the day they are needed, they
          are needed badly, and hunting for them in a release note is
          worse than one extra tap. */}
      <button onClick={() => setShowTools(!showTools)} style={{
        width: '100%', textAlign: 'left', background: 'var(--surface-1)',
        marginBottom: showTools ? 16 : 0, fontSize: 13, fontWeight: 500,
        color: 'var(--text-secondary)',
      }}>
        {showTools ? '▾' : '▸'} เครื่องมือขั้นสูง · ความถี่ซิงก์ ซ่อมข้อมูล
      </button>

      {showTools && (<>
      <div className="card" style={{ marginBottom: 16 }}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 12px' }}>ความถี่การซิงก์อัตโนมัติ</p>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '0 0 12px' }}>
          ดึงบิลใหม่จาก Loyverse แล้วตัดสต๊อกวัตถุดิบตามสูตรทุกกี่วินาที
        </p>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input type="number" value={intervalInput} onChange={(e) => setIntervalInput(e.target.value)}
            style={{ width: 100 }} />
          <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>วินาที</span>
          <button onClick={saveInterval} disabled={savingInterval}>
            {savingInterval ? 'กำลังบันทึก...' : 'บันทึก'}
          </button>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 12px' }}>ย้ายข้อมูลสต๊อกเข้าระบบใหม่</p>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '0 0 12px' }}>
          กดครั้งเดียวหลังอัปเดตเป็นเวอร์ชันใหม่ - ย้ายตัวเลขสต๊อกเดิมเข้าระบบบันทึกการเคลื่อนไหว
          เพื่อให้คำนวณต้นทุนเฉลี่ยและดูประวัติย้อนหลังได้ กดซ้ำได้ ไม่ทำให้ข้อมูลซ้ำ
        </p>
        <button onClick={runMigration} disabled={migrating || !storeId}>
          {migrating ? 'กำลังย้าย...' : 'ย้ายข้อมูลสต๊อก'}
        </button>
        {migrateResult && (
          <p style={{ fontSize: 12, color: 'var(--text-success)', marginTop: 8 }}>
            ย้ายแล้ว {migrateResult.migrated_materials} รายการ
          </p>
        )}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 12px' }}>คำนวณยอดคงเหลือใหม่</p>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '0 0 12px' }}>
          รวมยอดคงเหลือและต้นทุนเฉลี่ยของวัตถุดิบทุกตัวใหม่จากประวัติการเคลื่อนไหวทั้งหมด
          ทำให้หน้า “ของในครัว” เปิดเร็วขึ้นมาก และใช้ตรวจได้ว่าตัวเลขที่เห็นตรงกับประวัติจริง
          กดซ้ำได้ ไม่ทำให้ข้อมูลเพี้ยน
        </p>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '0 0 12px' }}>
          ควรกดตอนร้านไม่ยุ่ง — ถ้ามีบิลเข้ามาพอดีจังหวะที่กำลังคำนวณ ยอดของวัตถุดิบตัวนั้น
          อาจคลาดไปหนึ่งบิล กดซ้ำอีกครั้ง (หรือรอบนับสต๊อกถัดไป) จะตรงเอง
        </p>
        <button onClick={runRebuild} disabled={rebuilding || !storeId}>
          {rebuilding ? 'กำลังคำนวณ...' : 'คำนวณยอดคงเหลือใหม่'}
        </button>
        {rebuildResult && (
          <p style={{ fontSize: 12, color: 'var(--text-success)', marginTop: 8 }}>
            คำนวณใหม่แล้ว {rebuildResult.rebuilt_materials} รายการ
          </p>
        )}
      </div>

      <div className="card">
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 12px' }}>ซิงก์ตอนนี้</p>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '0 0 12px' }}>
          บังคับให้ซิงก์ทันทีโดยไม่ต้องรอรอบถัดไป - ดึงเฉพาะบิลใหม่ตั้งแต่ครั้งล่าสุดที่ซิงก์เท่านั้น
        </p>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={runSync} disabled={syncing || !storeId}>
            {syncing ? 'กำลังซิงก์...' : 'ซิงก์ตอนนี้'}
          </button>
          <button onClick={resetCursor} disabled={resettingCursor || !storeId}
            style={{ fontSize: 12 }}>
            {resettingCursor ? 'กำลังรีเซ็ต...' : 'รีเซ็ตจุดซิงก์'}
          </button>
        </div>
        {syncResult && (
          <p style={{ fontSize: 12, color: 'var(--text-success)', marginTop: 8 }}>
            ประมวลผลบิลใหม่ {syncResult.processed_receipts} รายการ
            {syncResult.processed_receipts === 0 && ' (ปกติสำหรับการซิงก์ครั้งแรกหลังเชื่อมสาขานี้ - รอบถัดไปจะเริ่มดึงบิลใหม่ตั้งแต่ตอนนี้เป็นต้นไป)'}
          </p>
        )}
        {syncError && (
          <p style={{ fontSize: 12, color: 'var(--text-danger)', marginTop: 8 }}>{syncError}</p>
        )}
      </div>
      </>)}
    </div>
  );
}

function formatDate(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleDateString('th-TH', { year: 'numeric', month: 'short', day: 'numeric' });
  } catch {
    return iso;
  }
}
