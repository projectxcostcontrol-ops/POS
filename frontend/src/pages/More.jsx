import { NavLink } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { MORE_GROUPS } from '../nav';
import { SignOut } from '@phosphor-icons/react';

/**
 * Everything that isn't daily work, grouped by how often it's actually
 * needed. On a phone the bottom bar holds the three daily jobs and this
 * page holds the rest; on a wide screen the sidebar shows the same
 * grouping and this page is rarely reached.
 *
 * Each entry keeps its old name in small text underneath during the
 * rename. Someone who learned "วิเคราะห์ส่วนต่าง" shouldn't have to
 * hunt for where it went - and once nobody needs the crutch, the `was`
 * fields come out of nav.js and this renders without them.
 */
export default function More() {
  const { can, profile, signOut } = useAuth();

  return (
    <div className="tab-more">
      <p style={{ fontSize: 15, fontWeight: 500, margin: '0 0 16px' }}>เพิ่มเติม</p>

      {MORE_GROUPS.map((group) => {
        const items = group.items.filter((i) => !i.needs || can(i.needs));
        if (items.length === 0) return null;
        return (
          <div key={group.title || 'main'}>
            {/* The first group has no heading - a label over the only
                list on screen names nothing. */}
            {group.title && <div className="nav-group">{group.title}</div>}
            {items.map((item) => {
              const Icon = item.icon;
              return (
                <NavLink key={item.to} to={item.to}>
                  <Icon className="nav-icon" size={22} />
                  <span>
                    <span className="label">{item.label}</span>
                    {item.was && <div className="was">เดิม: {item.was}</div>}
                  </span>
                  <span className="chev">›</span>
                </NavLink>
              );
            })}
          </div>
        );
      })}

      <div className="mobile-account">
        <div className="mobile-account-copy">
          <span>บัญชีที่ใช้งาน</span>
          <strong>{profile?.email}</strong>
        </div>
        <button type="button" className="mobile-signout" onClick={signOut}>
          <SignOut size={19} />
          ออกจากระบบ
        </button>
      </div>
    </div>
  );
}
