import { useState } from "react";
import axios from "axios";
import { setAccessToken } from "../lib/auth";

const API_BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

export function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const body = new URLSearchParams();
      body.set("username", username);
      body.set("password", password);

      const resp = await axios.post(`${API_BASE_URL}/auth/token`, body.toString(), {
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
      });
      setAccessToken(resp.data.access_token);
      window.location.assign("/");
    } catch {
      setError("Invalid credentials");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 bg-surface border border-border rounded-2xl p-6">
        <div>
          <p className="text-sm text-muted">Bayse Agent</p>
          <h1 className="text-2xl font-semibold">Sign in</h1>
        </div>

        <label className="block space-y-1">
          <span className="text-sm text-muted">Username</span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-full bg-[#0F1016] border border-border rounded-lg px-3 py-2"
            autoComplete="username"
          />
        </label>

        <label className="block space-y-1">
          <span className="text-sm text-muted">Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full bg-[#0F1016] border border-border rounded-lg px-3 py-2"
            autoComplete="current-password"
          />
        </label>

        {error && <p className="text-sm text-danger">{error}</p>}

        <button
          type="submit"
          disabled={loading}
          className="w-full px-4 py-2 rounded-lg bg-primary text-bg font-semibold disabled:opacity-60"
        >
          {loading ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </div>
  );
}
