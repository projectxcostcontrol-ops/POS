import { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom';
import { StoreProvider } from './store/StoreContext';
import { AuthProvider, useAuth } from './auth/AuthContext';
import { api } from './api/client';
import Login from './pages/Login';
import Signup from './pages/Signup';
import Dashboard from './pages/Dashboard';
import Items from './pages/Items';
import Materials from './pages/Materials';
import Receiving from './pages/Receiving';
import Recipes from './pages/Recipes';
import Receipts from './pages/Receipts';
import IncomeExpense from './pages/IncomeExpense';
import Settings from './pages/Settings';
import Users from './pages/Users';
import Admin from './pages/Admin';

// `needs` is the capability required to see a page. Pages without one are
// available to anyone signed in. This drives the menu only - the backend
// enforces the same rules independently, so hiding a link is convenience,
// not security.
const NAV = [
  { to: '/', label: 'หน้าแรก', end: true, needs: 'view_money' },
  { to: '/items', label: 'รายการสินค้า' },
  { to: '/materials', label: 'วัตถุดิบและสต๊อก' },
  { to: '/receiving', label: 'รับของเข้า' },
  { to: '/recipes', label: 'สูตรอาหาร' },
  { to: '/receipts', label: 'รายการบิล', needs: 'view_money' },
  { to: '/income-expense', label: 'รายรับรายจ่าย', needs: 'view_money' },
  { to: '/users', label: 'ผู้ใช้งาน', needs: 'manage_users' },
  { to: '/settings', label: 'ตั้งค่า', needs: 'manage_settings' },
];

export default function App() {
  return (
    <AuthProvider>
      <AuthGate />
    </AuthProvider>
  );
}

function AuthGate() {
  const { firebaseUser, profile, needsSignup, loading } = useAuth();

  if (loading) {
    return <div style={{ padding: 24 }}>กำลังโหลด...</div>;
  }
  if (!firebaseUser) {
    return <Login />;
  }
  // Signed in with Firebase but not part of a business yet - a normal
  // state for a new account, and the only way into one.
  if (needsSignup) {
    return <Signup />;
  }
  if (!profile) {
    return <Login />;   // something else went wrong; Login shows the reason
  }

  return (
    <StoreProvider>
      <AppShell />
    </StoreProvider>
  );
}

function AppShell() {
  const { profile, signOut, can } = useAuth();
  const [isAdmin, setIsAdmin] = useState(false);
  const visibleNav = NAV.filter((item) => !item.needs || can(item.needs));
  // Staff can't see the dashboard, so send them somewhere they can use.
  const homePath = can('view_money') ? '/' : '/materials';

  // The admin link only appears for the handful of emails configured on the
  // backend. Asking is harmless - a normal user gets a 404 and simply never
  // sees the link.
  useEffect(() => {
    api.adminWhoami().then(() => setIsAdmin(true)).catch(() => setIsAdmin(false));
  }, []);

  return (
    <BrowserRouter>
      <div className="app-shell">
        <nav className="sidebar">
          {profile.business_name && (
            <p style={{
              fontSize: 12, fontWeight: 500, margin: '0 0 12px',
              paddingBottom: 12, borderBottom: '0.5px solid var(--border)',
              wordBreak: 'break-word',
            }}>
              {profile.business_name}
            </p>
          )}
          {visibleNav.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end}
              className={({ isActive }) => (isActive ? 'active' : '')}>
              {item.label}
            </NavLink>
          ))}
          {isAdmin && (
            <NavLink to="/admin" className={({ isActive }) => (isActive ? 'active' : '')}>
              ภาพรวมระบบ
            </NavLink>
          )}
          <div style={{ marginTop: 'auto', paddingTop: 16, fontSize: 11, color: 'var(--text-muted)' }}>
            <p style={{ margin: '0 0 2px', wordBreak: 'break-all' }}>{profile.email}</p>
            <p style={{ margin: '0 0 8px' }}>
              {{ owner: 'เจ้าของ', manager: 'ผู้จัดการ', staff: 'พนักงาน' }[profile.role]}
            </p>
            <button onClick={signOut} style={{ fontSize: 11, width: '100%' }}>ออกจากระบบ</button>
          </div>
        </nav>
        <main className="content">
          <Routes>
            <Route path="/" element={can('view_money') ? <Dashboard /> : <Navigate to={homePath} replace />} />
            <Route path="/items" element={<Items />} />
            <Route path="/materials" element={<Materials />} />
            <Route path="/receiving" element={<Receiving />} />
            <Route path="/recipes" element={<Recipes />} />
            <Route path="/receipts" element={can('view_money') ? <Receipts /> : <Navigate to={homePath} replace />} />
            <Route path="/income-expense" element={can('view_money') ? <IncomeExpense /> : <Navigate to={homePath} replace />} />
            <Route path="/users" element={can('manage_users') ? <Users /> : <Navigate to={homePath} replace />} />
            <Route path="/settings" element={can('manage_settings') ? <Settings /> : <Navigate to={homePath} replace />} />
            <Route path="/admin" element={isAdmin ? <Admin /> : <Navigate to={homePath} replace />} />
            <Route path="*" element={<Navigate to={homePath} replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
