/* ──────────────────────────────────────────────
 * Client-side Providers
 * Wraps children with TanStack Query context.
 * ────────────────────────────────────────────── */

"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Retry once, then surface the error inline
            retry: 1,
            refetchOnWindowFocus: false,
            staleTime: 60_000, // 1 minute
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}
