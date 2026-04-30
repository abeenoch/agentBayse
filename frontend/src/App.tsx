import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import "./styles.css";
import { Dashboard } from "./pages/Dashboard";
import { Markets } from "./pages/Markets";
import { Signals } from "./pages/Signals";
import { Settings } from "./pages/Settings";
import { Login } from "./pages/Login";
import { useWebSocket } from "./hooks/useWebSocket";
import { clearAccessToken, getAccessToken } from "./lib/auth";

const navClasses = ({ isActive }: { isActive: boolean }) =>
  `px-3 py-2 rounded-lg text-sm ${isActive ? "bg-primary/20 text-primary" : "text-muted hover:text-text"}`;

function App() {
  useWebSocket();
  const token = getAccessToken();

  if (!token) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <div className="min-h-screen bg-bg text-text">
      <div className="mx-auto max-w-6xl px-4 py-6">
        <header className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-primary/30 flex items-center justify-center text-lg font-bold text-primary">
              PB
            </div>
            <div>
              <h1 className="text-xl font-semibold">
                <span className="text-primary">Agent</span> Bayse
              </h1>
            </div>
          </div>
          <nav className="flex gap-2 items-center">
            <NavLink to="/" className={navClasses} end>
              Dashboard
            </NavLink>
            <NavLink to="/markets" className={navClasses}>
              Markets
            </NavLink>
            <NavLink to="/signals" className={navClasses}>
              Signals
            </NavLink>
            <NavLink to="/settings" className={navClasses}>
              Settings
            </NavLink>
            <button
              onClick={() => {
                clearAccessToken();
                window.location.assign("/login");
              }}
              className="px-3 py-2 rounded-lg text-sm text-muted hover:text-text"
            >
              Logout
            </button>
          </nav>
        </header>

        <main>
          <Routes>
            <Route path="/login" element={<Navigate to="/" replace />} />
            <Route path="/" element={<Dashboard />} />
            <Route path="/markets" element={<Markets />} />
            <Route path="/signals" element={<Signals />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

export default App;
