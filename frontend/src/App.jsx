import { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom';
import { StoreProvider } from './store/StoreContext';
import { AuthProvider, useAuth } from './auth/AuthContext';
import { api } from './api/client';
import { SIDEBAR_GROUPS, TABS } from './nav';
import Login from './pages/Login';
import Signup from './pages/Signup';
import Dashboard from './pages/Dashboard';
import More from './pages/More';
import Items from './pages/Items';
import Materials from './pages/Materials';
import Receiving from './pages/Receiving';
import Recipes from './pages/Recipes';
import StockCount from './pages/StockCount';
import Variance from './pages/Variance';
import Receipts from './pages/Receipts';
import IncomeExpense from './pages/IncomeExpense';
import Settings from './pages/Settings';
import Users from './pages/Users';
import Admin from './pages/Admin';

const ROLE_LABEL = { owner: 'เจ้าของ', manager: 'ผู้จัดการ', staff: 'พนักงาน' };

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

  // Everyone lands on the home page now. It shows staff the alerts and
  // shortcuts they can act on, and hides the money figures - so there's
  // no longer a reason to bounce them somewhere else on login.
  const homePath = '/';

  // The admin link only appears for the handful of emails configured on the
  // backend. Asking is harmless - a normal user gets a 404 and simply never
  // sees the link.
  useEffect(() => {
    api.adminWhoami().then(() => setIsAdmin(true)).catch(() => setIsAdmin(false));
  }, []);

  const allowed = (item) => !item.needs || can(item.needs);

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

          {SIDEBAR_GROUPS.map((group) => {
            const items = group.items.filter(allowed);
            if (items.length === 0) return null;
            return (
              <div key={group.title}>
                <div className="nav-group">{group.title}</div>
                {items.map((item) => (
                  <NavLink key={item.to} to={item.to} end={item.end}
                    className={({ isActive }) => (isActive ? 'active' : '')}>
                    {item.label}
                  </NavLink>
                ))}
              </div>
            );
          })}

          {isAdmin && (
            <div>
              <div className="nav-group">ระบบ</div>
              <NavLink to="/admin" className={({ isActive }) => (isActive ? 'active' : '')}>
                ภาพรวมระบบ
              </NavLink>
            </div>
          )}

          <div style={{ marginTop: 'auto', paddingTop: 16, fontSize: 11, color: 'var(--text-muted)' }}>
            <p style={{ margin: '0 0 2px', wordBreak: 'break-all' }}>{profile.email}</p>
            <p style={{ margin: '0 0 8px' }}>{ROLE_LABEL[profile.role]}</p>
            <button onClick={signOut} style={{ fontSize: 11, width: '100%' }}>ออกจากระบบ</button>
          </div>
        </nav>

        <main className="content">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/more" element={<More />} />
            <Route path="/items" element={<Items />} />
            <Route path="/materials" element={<Materials />} />
            <Route path="/receiving" element={<Receiving />} />
            <Route path="/recipes" element={<Recipes />} />
            <Route path="/stock-count" element={<StockCount />} />
            <Route path="/variance" element={can('view_money') ? <Variance /> : <Navigate to={homePath} replace />} />
            <Route path="/receipts" element={can('view_money') ? <Receipts /> : <Navigate to={homePath} replace />} />
            <Route path="/income-expense" element={can('view_money') ? <IncomeExpense /> : <Navigate to={homePath} replace />} />
            <Route path="/users" element={can('manage_users') ? <Users /> : <Navigate to={homePath} replace />} />
            <Route path="/settings" element={can('manage_settings') ? <Settings /> : <Navigate to={homePath} replace />} />
            <Route path="/admin" element={isAdmin ? <Admin /> : <Navigate to={homePath} replace />} />
            <Route path="*" element={<Navigate to={homePath} replace />} />
          </Routes>
        </main>

        {/* Phone only - CSS hides this above 720px, where the sidebar takes over. */}
        <nav className="tabbar">
          {TABS.map((tab) => (
            <NavLink key={tab.to} to={tab.to} end={tab.end}
              className={({ isActive }) => (isActive ? 'active' : '')}>
              <span className="emoji">{tab.emoji}</span>
              {tab.short || tab.label}
            </NavLink>
          ))}
        </nav>
      </div>
    </BrowserRouter>
  );
}
