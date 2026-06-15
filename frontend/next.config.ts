import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  typedRoutes: false,
  turbopack: {
    root: process.cwd()
  },
  // Per-icon tree-shaking for the icon barrel. Without this, importing a few icons from
  // "@phosphor-icons/react" pulls the whole set into each route, which dominates the dev
  // compile time of heavy pages like /student. Rewrites imports to the exact icon modules.
  experimental: {
    optimizePackageImports: ["@phosphor-icons/react"]
  }
};

export default nextConfig;
