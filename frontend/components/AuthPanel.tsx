"use client";

import { useEffect, useState, type FormEvent } from "react";
import {
  ApiError,
  fetchMe,
  login,
  logout,
  register,
  type AuthUser,
} from "@/lib/api";

interface AuthPanelProps {
  user: AuthUser | null;
  onUserChange: (user: AuthUser | null) => void;
}

export default function AuthPanel({ user, onUserChange }: AuthPanelProps) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchMe()
      .then((me) => {
        if (!cancelled) onUserChange(me);
      })
      .catch(() => {
        if (!cancelled) onUserChange(null);
      });
    return () => {
      cancelled = true;
    };
  }, [onUserChange]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const auth =
        mode === "login"
          ? await login(email, password)
          : await register(email, password);
      onUserChange(auth.user);
      setOpen(false);
      setPassword("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Auth failed");
    } finally {
      setLoading(false);
    }
  }

  function handleLogout() {
    logout();
    onUserChange(null);
  }

  if (user) {
    return (
      <div className="flex items-center gap-2">
        <span
          className="hidden max-w-[140px] truncate text-xs text-slate-400 sm:inline"
          title={user.email}
        >
          {user.email}
        </span>
        <button
          type="button"
          onClick={handleLogout}
          className="rounded-lg border border-white/10 bg-white/5 px-2.5 py-1 text-xs font-medium text-slate-300 transition hover:border-white/20 hover:bg-white/10"
        >
          Sign out
        </button>
      </div>
    );
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="rounded-lg border border-violet-500/30 bg-violet-500/10 px-2.5 py-1 text-xs font-medium text-violet-200 transition hover:bg-violet-500/20"
      >
        Sign in
      </button>

      {open && (
        <div className="absolute right-0 top-full z-50 mt-2 w-72 rounded-xl border border-white/10 bg-[#0c0c14] p-4 shadow-xl">
          <div className="mb-3 flex gap-2">
            <button
              type="button"
              onClick={() => {
                setMode("login");
                setError(null);
              }}
              className={`flex-1 rounded-md px-2 py-1.5 text-xs font-medium ${
                mode === "login"
                  ? "bg-violet-500/20 text-violet-200"
                  : "text-slate-500 hover:text-slate-300"
              }`}
            >
              Sign in
            </button>
            <button
              type="button"
              onClick={() => {
                setMode("register");
                setError(null);
              }}
              className={`flex-1 rounded-md px-2 py-1.5 text-xs font-medium ${
                mode === "register"
                  ? "bg-violet-500/20 text-violet-200"
                  : "text-slate-500 hover:text-slate-300"
              }`}
            >
              Create account
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-3">
            <div>
              <label className="mb-1 block text-[10px] uppercase tracking-wide text-slate-500">
                Email
              </label>
              <input
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-sm text-slate-100 outline-none focus:border-violet-500/40"
              />
            </div>
            <div>
              <label className="mb-1 block text-[10px] uppercase tracking-wide text-slate-500">
                Password
              </label>
              <input
                type="password"
                required
                minLength={8}
                autoComplete={
                  mode === "login" ? "current-password" : "new-password"
                }
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-sm text-slate-100 outline-none focus:border-violet-500/40"
              />
            </div>
            {error && (
              <p className="text-xs text-red-300" role="alert">
                {error}
              </p>
            )}
            <button
              type="submit"
              disabled={loading}
              className="w-full rounded-lg bg-gradient-to-r from-violet-600 to-cyan-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
            >
              {loading
                ? "…"
                : mode === "login"
                  ? "Sign in"
                  : "Create account"}
            </button>
          </form>
          <p className="mt-3 text-[10px] leading-relaxed text-slate-600">
            Signed-in portfolios persist in the database. Guests share a local
            anonymous watchlist.
          </p>
        </div>
      )}
    </div>
  );
}
