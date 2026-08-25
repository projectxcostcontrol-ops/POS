import { useEffect, useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { useStore } from '../store/StoreContext';
import { api } from '../api/client';

const ROLE_LABELS = {
  owner: 'เจ้าของ - เห็นทุกอย่าง ทุกสาขา',
  manager: 'ผู้จัดการ - จัดการสาขาที่ได้รับมอบหมาย รวมข้อมูลการเงิน',
  staff: 'พนักงาน - สต๊อก/รับของ/สูตร ไม่เห็นข้อมูลการเงิน',
};
const ROLE_SHORT = { owner: 'เจ้าของ', manager: 'ผู้จัดการ', staff: 'พนักงาน' };

export default function Users() {
  const { profile } = useAuth();
  const { stores } = useStore();
  const [users, setUsers] = useState([]);
  const [invites, setInvites] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showInvite, setShowInvite] = useState(false);
  const [copied, setCopied] = useState('');

  function load() {
    setLoading(true);
    api.listUsers()
      .then((data) => { setUsers(data.users); setInvites(data.pending_invites); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }
  useEffect(load, []);

  const storeName = (id) => stores.find((s) => s.id === id)?.name || id;

  async function changeRole(uid, role, storeIds) {
    try {
      await api.updateUserRole(uid, role, storeIds);
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  async function removeUser(uid, email) {
    if (!window.confirm(`ลบ ${email} ออกจากระบบ?`)) return;
    try {
      await api.removeUser(uid);
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  async function cancelInvite(token) {
    try {
      await api.cancelInvite(token);
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  // The invite link is the whole delivery mechanism - there's no email
  // sending to configure, so the owner sends it however they already talk
  // to their staff (LINE, usually).
  const inviteLink = (token) => `${window.location.origin}/?invite=${token}`;

  async function copyLink(token) {
    try {
      await navigator.clipboard.writeText(inviteLink(token));
    } catch {
      window.prompt('คัดลอกลิงก์นี้', inviteLink(token));
    }
    setCopied(token);
    setTimeout(() => setCopied(''), 2000);
  }

  if (loading) return <p>กำลังโหลด...</p>;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
        <div>
          <p style={{ fontSize: 15, fontWeight: 500, margin: 0 }}>ผู้ใช้งาน</p>
          <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '2px 0 0' }}>
            {profile?.business_name} · {users.length} คน
          </p>
        </div>
        <button onClick={() => setShowInvite(true)}>+ เชิญผู้ใช้</button>
      </div>

      {error && <p style={{ fontSize: 12, color: 'var(--text-danger)', marginBottom: 12 }}>{error}</p>}

      <div className="card" style={{ marginBottom: 16 }}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 12px' }}>ผู้ใช้ปัจจุบัน ({users.length})</p>
        {users.map((u, idx) => (
          <div key={u.uid} style={{
            padding: '12px 0',
            borderBottom: idx < users.length - 1 ? '0.5px solid var(--border)' : 'none',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <p style={{ fontSize: 14, margin: 0 }}>
                  {u.email}
                  {u.uid === profile?.uid && (
                    <span style={{ fontSize: 11, color: 'var(--text-success)' }}> (คุณ)</span>
                  )}
                </p>
                <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '2px 0 0' }}>
                  {u.role === 'owner'
                    ? 'ทุกสาขา'
                    : (u.store_ids?.length
                      ? u.store_ids.map(storeName).join(', ')
                      : 'ยังไม่ได้กำหนดสาขา')}
                </p>
              </div>
              <select value={u.role}
                onChange={(e) => changeRole(u.uid, e.target.value, u.store_ids || [])}
                style={{ fontSize: 12 }}>
                {Object.keys(ROLE_SHORT).map((r) => <option key={r} value={r}>{ROLE_SHORT[r]}</option>)}
              </select>
              {u.uid !== profile?.uid && (
                <button onClick={() => removeUser(u.uid, u.email)} style={{ fontSize: 11, padding: '4px 8px' }}>
                  ลบ
                </button>
              )}
            </div>
            {u.role !== 'owner' && stores.length > 0 && (
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', paddingLeft: 4 }}>
                {stores.map((s) => {
                  const assigned = (u.store_ids || []).includes(s.id);
                  return (
                    <label key={s.id} style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 4 }}>
                      <input type="checkbox" checked={assigned}
                        onChange={() => {
                          const next = assigned
                            ? u.store_ids.filter((id) => id !== s.id)
                            : [...(u.store_ids || []), s.id];
                          changeRole(u.uid, u.role, next);
                        }} />
                      {s.name}
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        ))}
      </div>

      {invites.length > 0 && (
        <div className="card">
          <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 4px' }}>รอเข้าร่วม ({invites.length})</p>
          <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 12px' }}>
            ส่งลิงก์ให้เจ้าตัว - เปิดลิงก์แล้วสร้างบัญชีด้วยอีเมลที่ระบุไว้ถึงจะเข้าร่วมได้
          </p>
          {invites.map((inv, idx) => (
            <div key={inv.token} style={{
              padding: '10px 0',
              borderBottom: idx < invites.length - 1 ? '0.5px solid var(--border)' : 'none',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <span style={{ flex: 1, fontSize: 13, wordBreak: 'break-all' }}>{inv.email}</span>
                <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{ROLE_SHORT[inv.role]}</span>
                <button onClick={() => cancelInvite(inv.token)} style={{ fontSize: 11, padding: '4px 8px' }}>
                  ยกเลิก
                </button>
              </div>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <input readOnly value={inviteLink(inv.token)}
                  onFocus={(e) => e.target.select()}
                  style={{ flex: 1, fontSize: 11, minWidth: 0 }} />
                <button onClick={() => copyLink(inv.token)} style={{ fontSize: 11, padding: '4px 10px' }}>
                  {copied === inv.token ? 'คัดลอกแล้ว' : 'คัดลอก'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showInvite && (
        <InviteModal stores={stores} onCancel={() => setShowInvite(false)}
          onInvited={() => { setShowInvite(false); load(); }} />
      )}
    </div>
  );
}

function InviteModal({ stores, onCancel, onInvited }) {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('staff');
  const [storeIds, setStoreIds] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [created, setCreated] = useState(null);
  const [copied, setCopied] = useState(false);

  async function invite() {
    if (!email.trim()) { setError('ใส่อีเมลก่อน'); return; }
    setBusy(true);
    setError('');
    try {
      const res = await api.inviteUser(email.trim(), role, storeIds);
      setCreated(res);   // show the link here rather than closing straight away
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const link = created ? `${window.location.origin}/?invite=${created.token}` : '';

  async function copy() {
    try {
      await navigator.clipboard.writeText(link);
    } catch {
      window.prompt('คัดลอกลิงก์นี้', link);
    }
    setCopied(true);
  }

  if (created) {
    return (
      <div className="modal-overlay" onClick={onInvited}>
        <div className="modal-box" style={{ width: 340 }} onClick={(e) => e.stopPropagation()}>
          <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 4px' }}>สร้างคำเชิญแล้ว</p>
          <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 12px' }}>
            ส่งลิงก์นี้ให้ {created.email} - ใช้ได้ครั้งเดียว และต้องสมัครด้วยอีเมลนี้เท่านั้น
          </p>
          <input readOnly value={link} onFocus={(e) => e.target.select()}
            style={{ width: '100%', fontSize: 11, marginBottom: 12 }} />
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button onClick={onInvited}>ปิด</button>
            <button style={{ background: 'var(--surface-1)' }} onClick={copy}>
              {copied ? 'คัดลอกแล้ว' : 'คัดลอกลิงก์'}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-box" style={{ width: 340 }} onClick={(e) => e.stopPropagation()}>
        <p style={{ fontSize: 14, fontWeight: 500, margin: '0 0 4px' }}>เชิญผู้ใช้</p>
        <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 16px' }}>
          ระบบจะสร้างลิงก์ให้คัดลอกไปส่งเอง (ทาง LINE หรือช่องทางไหนก็ได้)
        </p>

        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>อีเมล</label>
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
          placeholder="staff@example.com" style={{ width: '100%', margin: '4px 0 12px' }} />

        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>สิทธิ์</label>
        <select value={role} onChange={(e) => setRole(e.target.value)}
          style={{ width: '100%', margin: '4px 0 4px' }}>
          <option value="staff">พนักงาน</option>
          <option value="manager">ผู้จัดการ</option>
          <option value="owner">เจ้าของ</option>
        </select>
        <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 0 12px' }}>
          {ROLE_LABELS[role]}
        </p>

        {role !== 'owner' && (
          <>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>สาขาที่เข้าถึงได้</label>
            <div style={{ margin: '4px 0 16px' }}>
              {stores.length === 0 && (
                <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>ยังไม่มีสาขา</p>
              )}
              {stores.map((s) => (
                <label key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, padding: '2px 0' }}>
                  <input type="checkbox" checked={storeIds.includes(s.id)}
                    onChange={() => setStoreIds(
                      storeIds.includes(s.id)
                        ? storeIds.filter((id) => id !== s.id)
                        : [...storeIds, s.id]
                    )} />
                  {s.name}
                </label>
              ))}
            </div>
          </>
        )}

        {error && <p style={{ fontSize: 12, color: 'var(--text-danger)', marginBottom: 12 }}>{error}</p>}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onCancel}>ยกเลิก</button>
          <button style={{ background: 'var(--surface-1)' }} onClick={invite} disabled={busy}>
            {busy ? 'กำลังเชิญ...' : 'เชิญ'}
          </button>
        </div>
      </div>
    </div>
  );
}
