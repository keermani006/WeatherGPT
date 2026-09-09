/* ──────────────────────────────────────────────
 * Shared Navigation Bar
 * Quiet text links — consistent with design system.
 * No icon-only nav unless labeled.
 * Health indicator using GET /health.
 * ────────────────────────────────────────────── */

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { getHealth } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard" },
  { href: "/chat", label: "Chat" },
  { href: "/alerts", label: "Alerts" },
  { href: "/climate", label: "Climate" },
];

export function Navbar() {
  const pathname = usePathname();
  const { user, isAuthenticated, logout, isLoading: authLoading } = useAuth();

  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: 30_000,
    retry: 0,
  });

  // Backend returns { status: "healthy" } — accept both forms
  const isHealthy = health?.status === "ok" || health?.status === "healthy";

  return (
    <nav
      className="w-full border-b border-hairline"
      role="navigation"
      aria-label="Main navigation"
    >
      <div className="max-w-3xl mx-auto px-6 flex items-center justify-between h-12">
        {/* Brand */}
        <Link
          href="/"
          className="font-mono text-sm font-semibold text-isobar hover:text-isobar/80 transition-colors"
        >
          WeatherGPT
        </Link>

        {/* Nav links */}
        <div className="flex items-center gap-4 sm:gap-5">
          {NAV_ITEMS.map((item) => {
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`font-sans text-xs sm:text-sm transition-colors ${
                  isActive
                    ? "text-isobar font-medium"
                    : "text-ink/50 hover:text-ink"
                }`}
                aria-current={isActive ? "page" : undefined}
              >
                {item.label}
              </Link>
            );
          })}

          <span className="w-px h-3.5 bg-hairline" />

          {/* Auth links or user menu */}
          {!authLoading && (
            <div className="flex items-center gap-3">
              {isAuthenticated && user ? (
                <div className="flex items-center gap-2.5">
                  <span
                    className="font-mono text-xs text-ink/70 max-w-[120px] truncate"
                    title={user.email}
                  >
                    {user.name || user.email.split("@")[0]}
                  </span>
                  <button
                    type="button"
                    onClick={() => logout()}
                    className="font-sans text-xs text-ink/40 hover:text-ochre transition-colors"
                  >
                    Logout
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  <Link
                    href="/login"
                    className={`font-sans text-xs sm:text-sm transition-colors ${
                      pathname === "/login"
                        ? "text-isobar font-medium"
                        : "text-ink/50 hover:text-ink"
                    }`}
                  >
                    Sign In
                  </Link>
                  <Link
                    href="/register"
                    className="font-sans text-xs px-2.5 py-1 bg-isobar text-paper font-medium hover:bg-isobar/90 transition-colors"
                  >
                    Register
                  </Link>
                </div>
              )}
            </div>
          )}

          {/* Health indicator — tristate dot */}
          <span
            className={`w-1.5 h-1.5 rounded-full shrink-0 transition-colors ${
              health === undefined
                ? "bg-hairline"          // loading — neutral
                : isHealthy
                  ? "bg-teal"            // connected
                  : "bg-ochre"           // failed
            }`}
            title={
              health === undefined
                ? "Checking backend…"
                : isHealthy
                  ? "Backend connected"
                  : "Backend unreachable"
            }
            aria-label={
              health === undefined
                ? "Checking backend…"
                : isHealthy
                  ? "Backend connected"
                  : "Backend unreachable"
            }
          />
        </div>
      </div>
    </nav>
  );
}

