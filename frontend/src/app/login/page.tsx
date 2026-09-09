/* ──────────────────────────────────────────────
 * Login Page — /login
 * WeatherGPT editorial sign-in page
 * ────────────────────────────────────────────── */

"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { ApiError } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const { login, loginDemo, isAuthenticated } = useAuth();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [demoLoading, setDemoLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // If already authenticated, redirect
  if (isAuthenticated) {
    router.replace("/alerts");
    return null;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!email.trim() || !password) {
      setError("Please fill in both email and password.");
      return;
    }

    setLoading(true);
    try {
      await login({ email: email.trim(), password });
      router.push("/alerts");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Invalid email or password. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  };

  const handleDemo = async () => {
    setError(null);
    setDemoLoading(true);
    try {
      await loginDemo();
      router.push("/alerts");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Failed to create demo session.");
      }
    } finally {
      setDemoLoading(false);
    }
  };

  return (
    <main className="flex-1 overflow-y-auto w-full max-w-md mx-auto px-6 py-12 flex flex-col justify-center">
      <div className="border border-hairline bg-paper/60 p-8 shadow-sm">
        {/* Header */}
        <div className="mb-6">
          <span className="font-mono text-xs uppercase tracking-widest text-isobar font-semibold">
            Authentication
          </span>
          <h1 className="font-sans text-2xl font-bold text-ink mt-1">Sign in to WeatherGPT</h1>
          <p className="font-sans text-xs text-ink/60 mt-1">
            Access your active meteorological alerts and synced settings.
          </p>
        </div>

        {/* Error notification */}
        {error && (
          <div className="mb-6 border-l-2 border-ochre bg-ochre/10 px-3 py-2 text-xs font-sans text-ochre">
            {error}
          </div>
        )}

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label className="block font-sans text-xs font-medium text-ink/70 mb-1" htmlFor="email">
              Email Address
            </label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="user@example.com"
              required
              className="w-full bg-transparent border-b border-hairline px-1 py-2 text-sm font-sans text-ink placeholder:text-ink/30 focus:border-isobar focus:outline-none transition-colors"
            />
          </div>

          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="block font-sans text-xs font-medium text-ink/70" htmlFor="password">
                Password
              </label>
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="font-sans text-xs text-ink/40 hover:text-ink transition-colors"
              >
                {showPassword ? "Hide" : "Show"}
              </button>
            </div>
            <input
              id="password"
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              required
              className="w-full bg-transparent border-b border-hairline px-1 py-2 text-sm font-sans text-ink placeholder:text-ink/30 focus:border-isobar focus:outline-none transition-colors"
            />
          </div>

          <div className="pt-2">
            <button
              type="submit"
              disabled={loading || demoLoading}
              className="w-full py-2.5 px-4 bg-isobar text-paper font-sans text-sm font-medium hover:bg-isobar/90 disabled:bg-ink/20 transition-all flex items-center justify-center gap-2"
            >
              {loading ? (
                <>
                  <span className="w-3.5 h-3.5 border-2 border-paper/30 border-t-paper rounded-full animate-spin" />
                  <span>Signing in…</span>
                </>
              ) : (
                "Sign In"
              )}
            </button>
          </div>
        </form>

        {/* Divider */}
        <div className="relative my-6 text-center">
          <hr className="border-t border-hairline" />
          <span className="absolute top-1/2 -translate-y-1/2 bg-paper px-3 font-mono text-[10px] uppercase text-ink/40">
            or explore
          </span>
        </div>

        {/* Demo button */}
        <button
          type="button"
          onClick={handleDemo}
          disabled={loading || demoLoading}
          className="w-full py-2 px-4 border border-hairline hover:border-isobar text-ink/80 hover:text-ink font-sans text-xs font-medium transition-colors flex items-center justify-center gap-2"
        >
          {demoLoading ? (
            <>
              <span className="w-3 h-3 border-2 border-ink/30 border-t-ink rounded-full animate-spin" />
              <span>Starting demo…</span>
            </>
          ) : (
            "⚡ Continue as Guest (1-Click Demo)"
          )}
        </button>

        {/* Register link */}
        <div className="mt-6 pt-4 border-t border-hairline text-center">
          <p className="font-sans text-xs text-ink/60">
            Don&apos;t have an account?{" "}
            <Link href="/register" className="text-isobar font-medium hover:underline">
              Create one now
            </Link>
          </p>
        </div>
      </div>
    </main>
  );
}
