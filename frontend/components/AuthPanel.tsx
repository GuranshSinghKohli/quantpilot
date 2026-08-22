"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Bracketed, HudButton } from "@/components/hud/primitives";
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
          className="osd-label hidden max-w-[140px] truncate sm:inline"
          title={user.email}
        >
          {user.email}
        </span>
        <HudButton variant="ghost" onClick={handleLogout}>
          Sign out
        </HudButton>
      </div>
    );
  }

  return (
    <div className="relative">
      <HudButton
        variant="secondary"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        Sign in
      </HudButton>

      {open && (
        <Bracketed
          large
          className="absolute right-0 top-full z-50 mt-2 w-[19rem] bg-letterbox p-4"
        >
          <div className="mb-4 flex gap-1.5">
            {(["login", "register"] as const).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => {
                  setMode(option);
                  setError(null);
                }}
                className={`bk bk-snap flex-1 px-2 py-2 font-display text-[0.6875rem] font-bold uppercase tracking-osd transition-colors ${
                  mode === option
                    ? "bk-active text-caution"
                    : "bk-quiet text-readout hover:text-osd"
                }`}
              >
                {option === "login" ? "Sign in" : "Create account"}
              </button>
            ))}
          </div>

          <form onSubmit={handleSubmit} className="space-y-3">
            <div>
              <label htmlFor="auth-email" className="osd-label mb-1.5 block">
                Email
              </label>
              <input
                id="auth-email"
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="bk w-full bg-letterbox px-3 py-2 text-sm text-osd caret-caution outline-none"
              />
            </div>
            <div>
              <label htmlFor="auth-password" className="osd-label mb-1.5 block">
                Password
              </label>
              <input
                id="auth-password"
                type="password"
                required
                minLength={8}
                autoComplete={
                  mode === "login" ? "current-password" : "new-password"
                }
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="bk w-full bg-letterbox px-3 py-2 text-sm text-osd caret-caution outline-none"
              />
              <p className="osd-label mt-1.5">At least 8 characters</p>
            </div>
            {error && (
              <p className="text-xs text-caution" role="alert">
                {error}
              </p>
            )}
            <HudButton
              type="submit"
              variant="primary"
              loading={loading}
              className="w-full"
            >
              {mode === "login" ? "Sign in" : "Create account"}
            </HudButton>
          </form>
          <p className="mt-3 text-[0.6875rem] leading-relaxed text-readout">
            An account keeps your holdings and case history. Without one,
            history stays on this device only.
          </p>
        </Bracketed>
      )}
    </div>
  );
}
