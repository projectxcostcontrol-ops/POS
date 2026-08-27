import { NavLink } from 'react-router-dom';
import { PlugsConnected } from '@phosphor-icons/react';

/**
 * What every page shows before the shop is connected to a POS.
 *
 * It replaces one grey sentence - "เลือกสาขาในหน้าตั้งค่าก่อน" - that
 * used to be the entire content of all eleven pages on a new account.
 * Three things were wrong with it, and only one of them was the tone.
 *
 * It named a page that does not exist. The menu has no "หน้าตั้งค่า";
 * the entry is เพิ่มเติม -> ตั้งค่าระบบ -> เชื่อมต่อ & สาขา, which
 * nobody who has just signed up is going to guess at.
 *
 * It was a dead end. No button, no link, nothing to press - on a screen
 * that was otherwise entirely blank.
 *
 * And it read like an error, when it is simply the first thing anyone
 * has to do.
 *
 * One line and one button. Deliberately not a checklist: at this point
 * there is exactly one thing to do, and a list of one item is a list
 * that exists to look thorough.
 */
export default function SetupGate({ what }) {
  return (
    <div style={{
      background: 'var(--surface-2)', border: '1px solid var(--border)',
      borderRadius: 12, padding: '22px 18px', textAlign: 'center',
      marginTop: 8,
    }}>
      <PlugsConnected size={30} weight="regular" style={{ color: 'var(--accent)' }} />
      <p style={{ fontSize: 14, fontWeight: 600, margin: '10px 0 4px' }}>
        เชื่อมต่อ Loyverse ก่อนถึงจะ{what || 'ใช้หน้านี้ได้'}
      </p>
      <p style={{ fontSize: 12.5, color: 'var(--text-secondary)', margin: '0 0 14px' }}>
        ใช้เวลาไม่ถึงนาที — วาง access token แล้วสาขาจะขึ้นมาเอง
      </p>
      {/* Styled here rather than with .button-primary: that class leans on
          the base `button` rule for its padding and height, and this is an
          anchor, so it came out as bare text inside a thin outline. */}
      <NavLink to="/settings" style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        textDecoration: 'none', background: 'var(--accent)', color: '#fff',
        fontWeight: 600, fontSize: 14, padding: '11px 20px', borderRadius: 10,
        minHeight: 44,
      }}>
        ไปเชื่อมต่อ Loyverse
      </NavLink>
    </div>
  );
}
