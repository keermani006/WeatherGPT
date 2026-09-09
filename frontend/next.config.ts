import type { NextConfig } from "next";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${BACKEND_URL}/api/v1/:path*`,
      },
      {
        source: "/health",
        destination: `${BACKEND_URL}/health`,
      },
      {
        source: "/health/ready",
        destination: `${BACKEND_URL}/health/ready`,
      },
    ];
  },
};

export default nextConfig;
