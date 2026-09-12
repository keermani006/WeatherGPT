/* ──────────────────────────────────────────────
 * Shared Navigation Bar
 * Responsive with mobile hamburger drawer and quiet desktop nav.
 * Health indicator using GET /health.
 * ────────────────────────────────────────────── */

"use client";

import { useState, useEffect } from "react";
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
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  // Close mobile menu whenever navigation occurs
  useEffect(() => {
    setMobileMenuOpen(false);
  }, [pathname]);

  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: 30_000,
    retry: 0,
  });

  // Backend returns { status: "healthy" } or "ok"
  const isHealthy = health?.status === "ok" || health?.status === "healthy";

  return (
    <nav
      className="w-full border-b border-hairline bg-paper relative z-30"
      role="navigation"
      aria-label="Main navigation"
    >
      <div className="max-w-3xl mx-auto px-4 sm:px-6 flex items-center justify-between h-12">
        {/* Brand */}
        <Link
          href="/"
          className="font-mono text-sm font-semibold text-isobar hover:text-isobar/80 transition-colors"
        >
          WeatherGPT
        </Link>

        {/* Desktop & Tablet Navigation (>= sm: 640px) */}
        <div className="hidden sm:flex items-center gap-4 md:gap-5">
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
                    className="font-sans text-xs text-ink/40 hover:text-ochre transition-colors cursor-pointer"
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
                ? "bg-hairline"
                : isHealthy
                  ? "bg-teal"
                  : "bg-ochre"
            }`}
            title={
              health === undefined
                ? "Checking backend…"
                : isHealthy
                  ? "Backend connected"
                  : "Backend unreachable"
            }
            aria-label="Backend status"
          />
        </div>

        {/* Mobile Nav Trigger & Health Dot (< sm: 640px) */}
        <div className="flex sm:hidden items-center gap-2.5">
          {/* Health indicator */}
          <span
            className={`w-1.5 h-1.5 rounded-full shrink-0 transition-colors ${
              health === undefined
                ? "bg-hairline"
                : isHealthy
                  ? "bg-teal"
                  : "bg-ochre"
            }`}
            title={isHealthy ? "Backend connected" : "Backend unreachable"}
          />

          {/* Hamburger toggle button */}
          <button
            type="button"
            onClick={() => setMobileMenuOpen((prev) => !prev)}
            className="p-1.5 text-ink/70 hover:text-ink transition-colors focus:outline-none"
            aria-label="Toggle navigation menu"
            aria-expanded={mobileMenuOpen}
          >
            {mobileMenuOpen ? (
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            ) : (
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            )}
          </button>
        </div>
      </div>

      {/* Mobile Menu Dropdown Drawer */}
      {mobileMenuOpen && (
        <div className="sm:hidden border-t border-hairline bg-paper/95 backdrop-blur-md px-5 py-4 space-y-3 shadow-lg animate-in fade-in slide-in-from-top-2 duration-150">
          <div className="flex flex-col space-y-2">
            {NAV_ITEMS.map((item) => {
              const isActive = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={() => setMobileMenuOpen(false)}
                  className={`font-sans text-sm py-2 px-2.5 rounded transition-colors flex items-center justify-between ${
                    isActive
                      ? "text-isobar font-semibold bg-isobar/10"
                      : "text-ink/70 hover:text-ink hover:bg-hairline/20"
                  }`}
                >
                  <span>{item.label}</span>
                  {isActive && <span className="w-1.5 h-1.5 rounded-full bg-isobar" />}
                </Link>
              );
            })}
          </div>

          <hr className="border-hairline" />

          {/* Mobile Auth Menu */}
          {!authLoading && (
            <div className="pt-1">
              {isAuthenticated && user ? (
                <div className="flex items-center justify-between px-2.5 py-1">
                  <div className="flex flex-col">
                    <span className="font-sans text-xs text-ink/50">Signed in as</span>
                    <span className="font-mono text-xs text-ink font-medium truncate max-w-[200px]">
                      {user.name || user.email}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      logout();
                      setMobileMenuOpen(false);
                    }}
                    className="font-sans text-xs px-3 py-1.5 border border-hairline text-ochre hover:bg-ochre/10 rounded transition-colors"
                  >
                    Logout
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-3 px-2 pt-1">
                  <Link
                    href="/login"
                    onClick={() => setMobileMenuOpen(false)}
                    className="flex-1 text-center py-2 text-xs font-sans border border-hairline text-ink font-medium rounded hover:bg-hairline/20 transition-colors"
                  >
                    Sign In
                  </Link>
                  <Link
                    href="/register"
                    onClick={() => setMobileMenuOpen(false)}
                    className="flex-1 text-center py-2 text-xs font-sans bg-isobar text-paper font-medium rounded hover:bg-isobar/90 transition-colors"
                  >
                    Register
                  </Link>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </nav>
  );
}
