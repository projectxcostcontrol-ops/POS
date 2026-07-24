import { useState } from 'react';
import { useAuth } from '../auth/AuthContext';

export default function Login() {
  const { signIn, signUp, profileError } = useAuth();
  // Someone arriving on an invite link needs to CREATE an account, not sign
  // in to one they don't have - so the link decides the starting mode.
  const hasInvite = new URLSearchParams(window.location.search).has('invite');
  const [mode, setMode] = useState(hasInvite ? 'signup' : 'signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      if (mode === 'signin') await signIn(email, password);
      else await signUp(email, password);
    } catch (err) {
      setError(translateFirebaseError(err.code) || err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'var(--bg)',
    }}>
      <form onSubmit={submit} className="card" style={{ width: 340 }}>
        <p style={{ fontSize: 18, fontWeight: 500, margin: '0 0 4px' }}>ระบบจัดการร้าน</p>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '0 0 20px' }}>
          {mode === 'signin'
            ? 'เข้าสู่ระบบเพื่อใช้งาน'
            : hasInvite
              ? 'สร้างบัญชีด้วยอีเมลที่ได้รับเชิญ'
              : 'สร้างบัญชีเพื่อเริ่มใช้งาน - ขั้นถัดไปจะให้ตั้งชื่อธุรกิจ'}
        </p>

        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>อีเมล</label>
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
          required autoComplete="email" style={{ width: '100%', margin: '4px 0 12px' }} />

        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>รหัสผ่าน</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
          required autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
          style={{ width: '100%', margin: '4px 0 16px' }} />

        {(error || profileError) && (
          <p style={{ fontSize: 12, color: 'var(--text-danger)', marginBottom: 12 }}>
            {error || profileError}
          </p>
        )}

        <button type="submit" disabled={busy}
          style={{ width: '100%', background: 'var(--surface-1)', marginBottom: 12 }}>
          {busy ? 'กำลังดำเนินการ...' : mode === 'signin' ? 'เข้าสู่ระบบ' : 'สร้างบัญชี'}
        </button>

        <p style={{ fontSize: 12, color: 'var(--text-secondary)', textAlign: 'center', margin: 0 }}>
          {mode === 'signin' ? 'ยังไม่มีบัญชี? ' : 'มีบัญชีแล้ว? '}
          <button type="button" onClick={() => { setMode(mode === 'signin' ? 'signup' : 'signin'); setError(''); }}
            style={{ background: 'none', padding: 0, color: 'var(--accent)', fontSize: 12 }}>
            {mode === 'signin' ? 'สร้างบัญชีธุรกิจใหม่' : 'เข้าสู่ระบบ'}
          </button>
        </p>
      </form>
    </div>
  );
}

function translateFirebaseError(code) {
  const messages = {
    'auth/invalid-credential': 'อีเมลหรือรหัสผ่านไม่ถูกต้อง',
    'auth/user-not-found': 'ไม่พบบัญชีนี้ - กด "สร้างบัญชีธุรกิจใหม่" ด้านล่าง',
    'auth/wrong-password': 'รหัสผ่านไม่ถูกต้อง',
    'auth/email-already-in-use': 'อีเมลนี้มีบัญชีแล้ว - กด "เข้าสู่ระบบ" แทน',
    'auth/weak-password': 'รหัสผ่านสั้นเกินไป - ต้องอย่างน้อย 6 ตัวอักษร',
    'auth/invalid-email': 'รูปแบบอีเมลไม่ถูกต้อง',
    'auth/too-many-requests': 'ลองผิดหลายครั้งเกินไป - รอสักครู่แล้วลองใหม่',
  };
  return messages[code];
}
