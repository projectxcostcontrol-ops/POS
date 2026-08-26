import { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, NavLink, Navigate, useLocation } from 'react-router-dom';
import { StoreProvider } from './store/StoreContext';
import { AuthProvider, useAuth } from './auth/AuthContext';
import { api } from './api/client';
import { DAILY, REGULAR, SETUP, TABS } from './nav';
import Login from './pages/Login';
import Signup from './pages/Signup';
import Dashboard from './pages/Dashboard';
import More from './pages/More';
import Items from './pages/Items';
import Materials from './pages/Materials';
import Receiving from './pages/Receiving';
import Recipes from './pages/Recipes';
import StockCount from './pages/StockCount';
import Receipts from './pages/Receipts';
import DeliveryOrders from './pages/DeliveryOrders';
import IncomeExpense from './pages/IncomeExpense';
import Settings from './pages/Settings';
import Users from './pages/Users';
import Admin from './pages/Admin';
import { BowlSteam, CaretDown, CaretRight, SignOut } from '@phosphor-icons/react';

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


  return (
    <BrowserRouter>
      <div className="app-shell">
        <Sidebar isAdmin={isAdmin} />

        <main className="content">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/more" element={<More />} />
            <Route path="/items" element={<Items />} />
            <Route path="/materials" element={<Materials />} />
            <Route path="/receiving" element={<Receiving />} />
            <Route path="/recipes" element={<Recipes />} />
            <Route path="/stock-count" element={<StockCount />} />
            {/* Merged into นับของ - kept as a redirect so an old bookmark
                or a link someone shared still lands somewhere useful. */}
            <Route path="/variance" element={<Navigate to="/stock-count" replace />} />
            <Route path="/delivery-orders" element={can('view_money') ? <DeliveryOrders /> : <Navigate to={homePath} replace />} />
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
          {TABS.map((tab) => {
            const Icon = tab.icon;
            return (
              <NavLink key={tab.to} to={tab.to} end={tab.end}
                className={({ isActive }) => (isActive ? 'active' : '')}>
                <Icon className="nav-icon" size={22} weight="regular" />
                {tab.short || tab.label}
              </NavLink>
            );
          })}
        </nav>
      </div>
    </BrowserRouter>
  );
}

/**
 * Lives inside the router so it can tell which page is open - the setup
 * group expands on its own when you're already on one of its pages,
 * rather than hiding the link you're currently looking at.
 */
function Sidebar({ isAdmin }) {
  const { profile, signOut, can } = useAuth();
  const [setupOpen, setSetupOpen] = useState(false);
  const location = useLocation();

  const allowed = (item) => !item.needs || can(item.needs);
  const setupItems = SETUP.items.filter(allowed);
  const onSetupPage = setupItems.some((i) => location.pathname.startsWith(i.to));
  const SetupIcon = SETUP.icon;

  return (
        <nav className="sidebar">
        <NavLink to="/" className="brand-lockup" aria-label="ล้านครัว หน้าแรก">
          <span className="brand-mark"><BowlSteam size={34} weight="light" /></span>
          <span><strong>ล้านครัว</strong><small>จัดการร้านอย่างรู้จริง</small></span>
        </NavLink>
        {profile.business_name && (
          <p className="business-chip">{profile.business_name}</p>
        )}

        {/* One flat list. The old headings ("ทุกวัน", "ทุกสัปดาห์",
            "ดูย้อนหลัง") took up more room than the two or three links
            under each, which made a short menu read as a filing system. */}
        {[...DAILY, ...REGULAR].filter(allowed).map((item) => {
          const Icon = item.icon;
          return (
            <NavLink key={item.to} to={item.to} end={item.end}
              className={({ isActive }) => (isActive ? 'active' : '')}>
              <Icon className="nav-icon" size={20} weight="regular" />
              <span>{item.label}</span>
            </NavLink>
          );
        })}

        {/* Setup collapses behind one entry - four links nobody opens
            twice shouldn't sit at the same weight as the daily work.
            Opens automatically when you're already on one of them, so
            the menu never hides where you currently are. */}
        {setupItems.length > 0 && (
          <>
            <button type="button" onClick={() => setSetupOpen(!setupOpen)}
              className={onSetupPage ? 'nav-toggle active' : 'nav-toggle'}>
              <SetupIcon className="nav-icon" size={20} weight="regular" />
              {SETUP.label}
              {setupOpen || onSetupPage
                ? <CaretDown className="nav-caret" size={14} />
                : <CaretRight className="nav-caret" size={14} />}
            </button>
            {(setupOpen || onSetupPage) && (
              <div className="nav-sub">
                {setupItems.map((item) => {
                  const Icon = item.icon;
                  return (
                    <NavLink key={item.to} to={item.to}
                      className={({ isActive }) => (isActive ? 'active' : '')}>
                      <Icon className="nav-icon" size={17} />
                      <span>{item.label}</span>
                    </NavLink>
                  );
                })}
              </div>
            )}
          </>
        )}

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
          <button onClick={signOut} className="signout-button"><SignOut size={17} />ออกจากระบบ</button>
        </div>
      </nav>
  );
}
