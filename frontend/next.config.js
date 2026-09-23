/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  async rewrites() {
    const api = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${api}/v1/:path*` }];
  },
  async headers() {
    const api = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    // Next.js exige inline scripts (bootstrap) e, no dev, eval (React Refresh).
    // Prod mantém o resto restrito; sem nonce, 'unsafe-inline' é o mínimo viável.
    const scriptSrc = process.env.NODE_ENV === "development"
      ? "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
      : "script-src 'self' 'unsafe-inline'";
    return [{
      source: "/:path*",
      headers: [{
        key: "Content-Security-Policy",
        value: `default-src 'self'; img-src 'self' data: blob:; ${scriptSrc}; style-src 'self' 'unsafe-inline'; worker-src 'self' blob:; connect-src 'self' ${api}; frame-ancestors 'none'`,
      }],
    }];
  },
};

module.exports = nextConfig;
