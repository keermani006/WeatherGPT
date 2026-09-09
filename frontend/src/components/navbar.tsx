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

const NAV_ITEMS = [
  { href: "/", label: "Dashboard" },
  { href: "/chat", label: "Chat" },
  { href: "/alerts", label: "Alerts" },
  { href: "/climate", label: "Climate" },
];

export function Navbar() {
  const pathname = usePathname();

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
      <div className="max-w-2xl mx-auto px-6 flex items-center justify-between h-12">
        {/* Brand */}
        <Link
          href="/"
          className="font-mono text-sm font-semibold text-isobar hover:text-isobar/80 transition-colors"
        >
          WeatherGPT
        </Link>

        {/* Nav links */}
        <div className="flex items-center gap-5">
          {NAV_ITEMS.map((item) => {
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`font-sans text-sm transition-colors ${
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

          {/* Health indicator — tristate dot
              teal   = backend responded with status ok
              ochre  = health check failed / unreachable (error only)
              hairline = still loading, not yet determined          */}
          <span
            className={`w-1.5 h-1.5 rounded-full shrink-0 transition-colors ${
              health === undefined
                ? "bg-hairline"          // loading — neutral, not a warning
                : isHealthy
                  ? "bg-teal"            // connected
                  : "bg-ochre"           // failed — only then ochre
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
