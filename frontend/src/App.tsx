import { useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import "./styles.css";
import { Dashboard } from "./pages/Dashboard";
import { Bayes } from "./pages/Bayes";
import { Markets } from "./pages/Markets";
import { Signals } from "./pages/Signals";
import { Settings } from "./pages/Settings";
import { Login } from "./pages/Login";
import { useWebSocket } from "./hooks/useWebSocket";
import { clearAccessToken, getAccessToken } from "./lib/auth";

const navClasses = ({ isActive }: { isActive: boolean }) =>
  `px-3 py-2 rounded-lg text-sm ${isActive ? "bg-primary/20 text-primary" : "text-muted hover:text-text"}`;

const mobileNavClasses = ({ isActive }: { isActive: boolean }) =>
  `block px-4 py-3 rounded-lg text-sm ${isActive ? "bg-primary/20 text-primary" : "text-muted hover:text-text"}`;

function App() {
  useWebSocket();
  const token = getAccessToken();
  const [menuOpen, setMenuOpen] = useState(false);

  if (!token) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  const logout = () => {
    clearAccessToken();
    window.location.assign("/login");
  };

  return (
    <div className="min-h-screen bg-bg text-text">
      <div className="mx-auto max-w-6xl px-4 py-4 sm:py-6">
        <header className="relative flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-primary/30 flex items-center justify-center text-xl">
              🕵️
            </div>
            <div>
              <h1 className="text-xl font-semibold">
                <span className="text-primary">Agent</span> Bayse
              </h1>
            </div>
          </div>

          {/* Desktop nav */}
          <nav className="hidden md:flex gap-2 items-center">
            <NavLink to="/" className={navClasses} end>
              Dashboard
            </NavLink>
            <NavLink to="/markets" className={navClasses}>
              Markets
            </NavLink>
            <NavLink to="/signals" className={navClasses}>
              Signals
            </NavLink>
            <NavLink to="/bayes" className={navClasses}>
              Bayes
            </NavLink>
            <NavLink to="/settings" className={navClasses}>
              Settings
            </NavLink>
            <button
              onClick={logout}
              className="px-3 py-2 rounded-lg text-sm text-muted hover:text-text"
            >
              Logout
            </button>
          </nav>

          {/* Mobile hamburger toggle */}
          <button
            className="relative z-40 md:hidden p-2 rounded-lg border border-border"
            onClick={() => setMenuOpen((open) => !open)}
            aria-label="Toggle navigation menu"
            aria-expanded={menuOpen}
          >
            {menuOpen ? "✕" : "☰"}
          </button>

          {/* Mobile menu */}
          {menuOpen && (
            <>
              <button
                className="fixed inset-0 z-30 cursor-default md:hidden"
                onClick={() => setMenuOpen(false)}
                aria-label="Close navigation menu"
              />
              <nav className="absolute top-full right-0 z-40 mt-2 w-48 space-y-1 rounded-xl border border-border bg-surface p-2 shadow-2xl md:hidden">
                <NavLink to="/" className={mobileNavClasses} end onClick={() => setMenuOpen(false)}>
                  Dashboard
                </NavLink>
                <NavLink to="/markets" className={mobileNavClasses} onClick={() => setMenuOpen(false)}>
                  Markets
                </NavLink>
                <NavLink to="/signals" className={mobileNavClasses} onClick={() => setMenuOpen(false)}>
                  Signals
                </NavLink>
                <NavLink to="/bayes" className={mobileNavClasses} onClick={() => setMenuOpen(false)}>
                  Bayes
                </NavLink>
                <NavLink to="/settings" className={mobileNavClasses} onClick={() => setMenuOpen(false)}>
                  Settings
                </NavLink>
                <button
                  onClick={logout}
                  className="block w-full px-4 py-3 text-left rounded-lg text-sm text-muted hover:text-text"
                >
                  Logout
                </button>
              </nav>
            </>
          )}
        </header>

        <main>
          <Routes>
            <Route path="/login" element={<Navigate to="/" replace />} />
            <Route path="/" element={<Dashboard />} />
            <Route path="/markets" element={<Markets />} />
            <Route path="/signals" element={<Signals />} />
            <Route path="/bayes" element={<Bayes />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

export default App;
