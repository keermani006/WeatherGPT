/* ──────────────────────────────────────────────
 * Register Page — /register
 * WeatherGPT editorial sign-up page
 * ────────────────────────────────────────────── */

"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { ApiError } from "@/lib/api";

export default function RegisterPage() {
  const router = useRouter();
  const { register, loginDemo, isAuthenticated } = useAuth();

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [demoLoading, setDemoLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // If already authenticated, redirect
  useEffect(() => {
    if (isAuthenticated) {
      router.replace("/alerts");
    }
  }, [isAuthenticated, router]);

  if (isAuthenticated) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    const cleanEmail = email.trim();
    if (!cleanEmail || !password) {
      setError("Please fill in all required fields.");
      return;
    }

    if (password.length < 6) {
      setError("Password must be at least 6 characters long.");
      return;
    }

    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    setLoading(true);
    try {
      await register({ email: cleanEmail, password, name: name.trim() || undefined });
      router.push("/alerts");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Registration failed. Please try again.");
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
    <main className="flex-1 overflow-y-auto w-full max-w-md mx-auto px-4 sm:px-6 py-8 sm:py-12 flex flex-col justify-center">
      <div className="border border-hairline bg-paper/60 p-6 sm:p-8 shadow-sm">
        {/* Header */}
        <div className="mb-6">
          <span className="font-mono text-xs uppercase tracking-widest text-teal font-semibold">
            Join WeatherGPT
          </span>
          <h1 className="font-sans text-xl sm:text-2xl font-bold text-ink mt-1">Create an Account</h1>
          <p className="font-sans text-xs text-ink/60 mt-1">
            Real-time threshold alerts, persistent search history, and AI insights.
          </p>
        </div>

        {/* Error notification */}
        {error && (
          <div className="mb-6 border-l-2 border-ochre bg-ochre/10 px-3 py-2 text-xs font-sans text-ochre">
            {error}
          </div>
        )}

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block font-sans text-xs font-medium text-ink/70 mb-1" htmlFor="name">
              Full Name (Optional)
            </label>
            <input
              id="name"
              type="text"
              autoComplete="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Jane Doe"
              className="w-full bg-transparent border-0 border-b border-hairline px-1 py-1.5 sm:py-2 text-base sm:text-sm font-sans text-ink placeholder:text-ink/30 focus:border-isobar outline-none ring-0 shadow-none focus:outline-none focus:ring-0 transition-colors"
            />
          </div>

          <div>
            <label className="block font-sans text-xs font-medium text-ink/70 mb-1" htmlFor="email">
              Email Address *
            </label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="jane@example.com"
              required
              className="w-full bg-transparent border-0 border-b border-hairline px-1 py-1.5 sm:py-2 text-base sm:text-sm font-sans text-ink placeholder:text-ink/30 focus:border-isobar outline-none ring-0 shadow-none focus:outline-none focus:ring-0 transition-colors"
            />
          </div>

          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="block font-sans text-xs font-medium text-ink/70" htmlFor="password">
                Password * (min 6 characters)
              </label>
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="font-sans text-xs text-ink/40 hover:text-ink transition-colors cursor-pointer"
              >
                {showPassword ? "Hide" : "Show"}
              </button>
            </div>
            <input
              id="password"
              type={showPassword ? "text" : "password"}
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              required
              className="w-full bg-transparent border-0 border-b border-hairline px-1 py-1.5 sm:py-2 text-base sm:text-sm font-sans text-ink placeholder:text-ink/30 focus:border-isobar outline-none ring-0 shadow-none focus:outline-none focus:ring-0 transition-colors"
            />
          </div>

          <div>
            <label className="block font-sans text-xs font-medium text-ink/70 mb-1" htmlFor="confirmPassword">
              Confirm Password *
            </label>
            <input
              id="confirmPassword"
              type={showPassword ? "text" : "password"}
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="••••••••"
              required
              className="w-full bg-transparent border-0 border-b border-hairline px-1 py-1.5 sm:py-2 text-base sm:text-sm font-sans text-ink placeholder:text-ink/30 focus:border-isobar outline-none ring-0 shadow-none focus:outline-none focus:ring-0 transition-colors"
            />
          </div>

          <div className="pt-3">
            <button
              type="submit"
              disabled={loading || demoLoading}
              className="w-full py-2.5 px-4 bg-teal text-paper font-sans text-sm font-medium hover:bg-teal/90 disabled:bg-ink/20 transition-all flex items-center justify-center gap-2"
            >
              {loading ? (
                <>
                  <span className="w-3.5 h-3.5 border-2 border-paper/30 border-t-paper rounded-full animate-spin" />
                  <span>Registering…</span>
                </>
              ) : (
                "Create Account"
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
          className="w-full py-2 px-4 border border-hairline hover:border-teal text-ink/80 hover:text-ink font-sans text-xs font-medium transition-colors flex items-center justify-center gap-2"
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

        {/* Login link */}
        <div className="mt-6 pt-4 border-t border-hairline text-center">
          <p className="font-sans text-xs text-ink/60">
            Already have an account?{" "}
            <Link href="/login" className="text-teal font-medium hover:underline">
              Sign in
            </Link>
          </p>
        </div>
      </div>
    </main>
  );
}
