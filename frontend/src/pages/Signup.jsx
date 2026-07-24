import { useEffect, useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { api } from '../api/client';

/**
 * Shown to someone who has signed in but doesn't belong to a business yet.
 *
 * Two doors, and which one they get is decided by whether the URL carries
 * an invite token - not by a choice they make. Someone who was invited
 * shouldn't have to know they're "joining" rather than "creating"; they
 * clicked a link and it should just work.
 */
export default function Signup() {
  const { firebaseUser, signOut, reloadProfile } = useAuth();
  const inviteToken = new URLSearchParams(window.location.search).get('invite');

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center',
      justifyContent: 'center', background: 'var(--bg)', padding: 16,
    }}>
      <div style={{ width: 360 }}>
        {inviteToken
          ? <JoinBusiness token={inviteToken} onDone={reloadProfile} />
          : <CreateBusiness onDone={reloadProfile} />}

        <p style={{ fontSize: 11, color: 'var(--text-muted)', textAlign: 'center', marginTop: 16 }}>
          เข้าสู่ระบบเป็น {firebaseUser?.email}{' '}
          <button type="button" onClick={signOut}
            style={{ background: 'none', padding: 0, color: 'var(--accent)', fontSize: 11 }}>
            เปลี่ยนบัญชี
          </button>
        </p>
      </div>
    </div>
  );
}

function CreateBusiness({ onDone }) {
  const [businessName, setBusinessName] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api.signupBusiness(businessName.trim(), displayName.trim());
      await onDone();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="card">
      <p style={{ fontSize: 18, fontWeight: 500, margin: '0 0 4px' }}>สร้างบัญชีธุรกิจใหม่</p>
      <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '0 0 20px' }}>
        ข้อมูลของธุรกิจคุณจะแยกจากร้านอื่นทั้งหมด และคุณจะเป็นเจ้าของบัญชีนี้
      </p>

      <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>ชื่อธุรกิจ / ชื่อร้าน</label>
      <input value={businessName} onChange={(e) => setBusinessName(e.target.value)}
        placeholder="เช่น ร้านอาหารบ้านสวน" required autoFocus
        style={{ width: '100%', margin: '4px 0 12px' }} />

      <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>ชื่อของคุณ</label>
      <input value={displayName} onChange={(e) => setDisplayName(e.target.value)}
        placeholder="ชื่อที่จะแสดงในระบบ"
        style={{ width: '100%', margin: '4px 0 16px' }} />

      {error && <p style={{ fontSize: 12, color: 'var(--text-danger)', marginBottom: 12 }}>{error}</p>}

      <button type="submit" disabled={busy}
        style={{ width: '100%', background: 'var(--surface-1)' }}>
        {busy ? 'กำลังสร้าง...' : 'สร้างบัญชีธุรกิจ'}
      </button>

      <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '12px 0 0', textAlign: 'center' }}>
        ถ้าคุณได้รับลิงก์คำเชิญจากเจ้าของร้าน ให้เปิดลิงก์นั้นแทนหน้านี้
      </p>
    </form>
  );
}

function JoinBusiness({ token, onDone }) {
  const { firebaseUser } = useAuth();
  const [invite, setInvite] = useState(null);
  const [displayName, setDisplayName] = useState('');
  const [loadError, setLoadError] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api.peekInvite(token)
      .then(setInvite)
      .catch((e) => setLoadError(e.message));
  }, [token]);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api.signupJoin(token, displayName.trim());
      // Drop the token from the URL so a refresh doesn't retry a consumed invite
      window.history.replaceState({}, '', window.location.pathname);
      await onDone();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  if (loadError) {
    return (
      <div className="card">
        <p style={{ fontSize: 15, fontWeight: 500, margin: '0 0 8px' }}>คำเชิญใช้ไม่ได้</p>
        <p style={{ fontSize: 12, color: 'var(--text-danger)', margin: 0 }}>{loadError}</p>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '8px 0 0' }}>
          ขอลิงก์ใหม่จากเจ้าของร้านได้เลย
        </p>
      </div>
    );
  }
  if (!invite) return <div className="card"><p style={{ fontSize: 13, margin: 0 }}>กำลังโหลดคำเชิญ...</p></div>;

  // The invite is issued to one specific email. Signing in as someone else
  // and joining anyway would let a forwarded link become a way in.
  const emailMismatch = invite.email !== (firebaseUser?.email || '').toLowerCase();
  const roleLabel = { owner: 'เจ้าของ', manager: 'ผู้จัดการ', staff: 'พนักงาน' }[invite.role] || invite.role;

  return (
    <form onSubmit={submit} className="card">
      <p style={{ fontSize: 18, fontWeight: 500, margin: '0 0 4px' }}>
        เข้าร่วม "{invite.business_name}"
      </p>
      <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '0 0 20px' }}>
        คุณได้รับเชิญให้เข้าใช้งานในตำแหน่ง{roleLabel}
      </p>

      <div style={{
        background: 'var(--surface-1)', borderRadius: 8, padding: '10px 14px',
        marginBottom: 16, fontSize: 12,
      }}>
        <p style={{ margin: '0 0 4px', color: 'var(--text-secondary)' }}>อีเมลที่ได้รับเชิญ</p>
        <p style={{ margin: 0, wordBreak: 'break-all' }}>{invite.email}</p>
      </div>

      {emailMismatch ? (
        <p style={{ fontSize: 12, color: 'var(--text-danger)', margin: 0 }}>
          คุณกำลังเข้าสู่ระบบด้วย {firebaseUser?.email} ซึ่งไม่ตรงกับอีเมลที่ได้รับเชิญ
          - กด "เปลี่ยนบัญชี" ด้านล่างแล้วเข้าใหม่ด้วย {invite.email}
        </p>
      ) : (
        <>
          <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>ชื่อของคุณ</label>
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)}
            placeholder="ชื่อที่จะแสดงในระบบ" autoFocus
            style={{ width: '100%', margin: '4px 0 16px' }} />

          {error && <p style={{ fontSize: 12, color: 'var(--text-danger)', marginBottom: 12 }}>{error}</p>}

          <button type="submit" disabled={busy}
            style={{ width: '100%', background: 'var(--surface-1)' }}>
            {busy ? 'กำลังเข้าร่วม...' : 'เข้าร่วม'}
          </button>
        </>
      )}
    </form>
  );
}
