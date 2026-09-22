/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  async rewrites() {
    const api = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${api}/v1/:path*` }];
  },
  async headers() {
    const api = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    return [{
      source: "/:path*",
      headers: [{
        key: "Content-Security-Policy",
        value: `default-src 'self'; img-src 'self' data: blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ${api}; frame-ancestors 'none'`,
      }],
    }];
  },
};

module.exports = nextConfig;
