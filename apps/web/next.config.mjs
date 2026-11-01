/** Static export — раздаётся FastAPI как статика в одном контейнере (docs/09 §9.10). */

const nextConfig = {
  output: "export",
  images: { unoptimized: true },
  async rewrites() {
    // Работает только в dev; в проде API на том же origin
    const api = process.env.TOKLIB_API_URL || "http://127.0.0.1:8000";
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
};

export default nextConfig;
