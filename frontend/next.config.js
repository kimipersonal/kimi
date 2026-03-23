/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  async rewrites() {
    const backend = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
    return [
      { source: '/api/:path*', destination: `${backend}/api/:path*` },
      { source: '/ws/:path*', destination: `${backend}/ws/:path*` },
    ]
  },
}

module.exports = nextConfig
