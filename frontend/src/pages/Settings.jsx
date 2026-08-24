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
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState('');
  const [disconnecting, setDisconnecting] = useState(false);
  const [intervalInput, setIntervalInput] = useState('');
  const [savingInterval, setSavingInterval] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState(null);
  const [syncError, setSyncError] = useState('');
  const [resettingCursor, setResettingCursor] = useState(false);
  const [recon, setRecon] = useState(null);
  const [checking, setChecking] = useState(false);
  const [resyncing, setResyncing] = useState(false);
  const [reconError, setReconError] = useState('');
  const [migrating, setMigrating] = useState(false);
  const [migrateResult, setMigrateResult] = useState(null);

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

  function loadAppSettings() {
    api.getAppSettings().then((s) => {
      setAppSettings(s);
      setIntervalInput(String(s.sync_interval_seconds));
      setNameInput(s.business_name || '');
    });
  }
  useEffect(loadAppSettings, []);

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

  async function connect() {
    if (!tokenInput.trim()) return;
    setConnecting(true);
    setConnectError('');
    try {
      await api.saveToken(tokenInput.trim());
      setTokenInput('');
      loadAppSettings();
      await refreshStores(); // no full page reload - avoids the SPA-route 404
    } catch (e) {
      setConnectError(e.message);
    } finally {
      setConnecting(false);
    }
  }

  async function disconnect() {
    setDisconnecting(true);
    setConnectError('');
    try {
      await api.disconnectToken();
      clearStores();
      loadAppSettings();
    } catch (e) {
      setConnectError(`ยกเลิกไม่สำเร็จ: ${e.message}`);
    } finally {
      setDisconnecting(false);
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

  async function runReconcile() {
    setChecking(true);
    setReconError('');
    try {
      setRecon(await api.reconcileSales(storeId, 1));
    } catch (e) {
      setRecon(null);
      setReconError(e.message);
    } finally {
      setChecking(false);
    }
  }

  async function runRepair() {
    setResyncing(true);
    setReconError('');
    try {
      await api.repairSales(storeId);
      // Re-check straight away so the result is visible rather than
      // leaving someone to wonder whether it worked.
      setRecon(await api.reconcileSales(storeId, 1));
    } catch (e) {
      setReconError(e.message);
    } finally {
      setResyncing(false);
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

      <div className="card" style={{ marginBottom: 16 }}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 12px' }}>เชื่อมต่อ Loyverse</p>
        {appSettings && (
          <p style={{ fontSize: 13, marginBottom: 12 }}>
            สถานะ:{' '}
            {appSettings.connected
              ? <span style={{ color: 'var(--text-success)' }}>เชื่อมต่อแล้ว</span>
              : <span style={{ color: 'var(--text-danger)' }}>ยังไม่ได้เชื่อมต่อ</span>}
          </p>
        )}

        {appSettings && appSettings.connected ? (
          <button onClick={disconnect} disabled={disconnecting}>
            {disconnecting ? 'กำลังยกเลิก...' : 'ยกเลิกการเชื่อมต่อ'}
          </button>
        ) : (
          <>
            <div style={{
              background: 'var(--surface-1)', borderRadius: 8, padding: '10px 14px',
              marginBottom: 12, fontSize: 12, color: 'var(--text-secondary)',
            }}>
              <p style={{ margin: '0 0 6px', fontWeight: 500 }}>วิธีสร้าง Access Token:</p>
              <p style={{ margin: '0 0 2px' }}>1. เข้า Loyverse Back Office</p>
              <p style={{ margin: '0 0 2px' }}>2. ไปที่ Settings → Access Tokens</p>
              <p style={{ margin: 0 }}>3. กด "เพิ่ม Access Token" แล้วคัดลอกมาวางด้านล่าง</p>
            </div>
            <button onClick={() => window.open('https://r.loyverse.com/dashboard/#/settings/access-tokens', '_blank')}
              style={{ marginBottom: 12 }}>
              ไปที่ Loyverse Back Office
            </button>
            <div>
              <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Access token</label>
              <input type="password" value={tokenInput} onChange={(e) => setTokenInput(e.target.value)}
                placeholder="วาง Loyverse access token ที่นี่"
                style={{ width: '100%', margin: '4px 0 12px' }} />
              <button onClick={connect} disabled={connecting}>
                {connecting ? 'กำลังเชื่อมต่อ...' : 'เชื่อมต่อ'}
              </button>
            </div>
          </>
        )}
        {connectError && (
          <p style={{ fontSize: 12, color: 'var(--text-danger)', marginTop: 8 }}>{connectError}</p>
        )}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 4px' }}>สาขาที่ใช้งาน</p>
        <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 12px' }}>
          ข้อมูลสต๊อก สูตร และรายรับรายจ่ายของแต่ละสาขาแยกจากกันทั้งหมด
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
            {s.name}
            {storeId === s.id && <span style={{ fontSize: 11, color: 'var(--text-success)' }}>กำลังใช้งาน</span>}
          </label>
        ))}
      </div>

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
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 4px' }}>ตรวจสอบยอดขาย</p>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '0 0 12px' }}>
          เทียบยอดใน Loyverse กับที่ระบบเก็บไว้ ถ้าไม่ตรง กด "ดึงประวัติทั้งหมดใหม่"
          เพื่อเก็บบิลที่ตกหล่น (ดึงได้เท่าที่ Loyverse ยังเก็บไว้ให้)
        </p>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button onClick={runReconcile} disabled={checking || !storeId}>
            {checking ? 'กำลังตรวจ...' : 'ตรวจสอบวันนี้'}
          </button>
          <button onClick={runRepair} disabled={resyncing || !storeId}>
            {resyncing ? 'กำลังดึงซ้ำ...' : 'ดึงประวัติทั้งหมดใหม่'}
          </button>
        </div>

        {recon && (
          <div style={{
            marginTop: 12, fontSize: 12,
            color: recon.missing_count > 0 ? 'var(--text-warning)' : 'var(--text-success)',
          }}>
            <div>Loyverse: {recon.pos.count} บิล · ฿{recon.pos.total.toLocaleString()}</div>
            <div>ในระบบ: {recon.saved.count} บิล · ฿{recon.saved.total.toLocaleString()}</div>
            {recon.missing_count > 0 ? (
              <div style={{ marginTop: 6 }}>
                ⚠ ขาดไป {recon.missing_count} บิล — กด "ดึงประวัติทั้งหมดใหม่" เพื่อเก็บเพิ่ม
              </div>
            ) : (
              <div style={{ marginTop: 6 }}>✓ ตรงกันแล้ว</div>
            )}
          </div>
        )}
        {reconError && (
          <p style={{ fontSize: 12, color: 'var(--text-danger)', marginTop: 8 }}>{reconError}</p>
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
