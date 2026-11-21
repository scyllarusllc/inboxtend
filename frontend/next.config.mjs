const backend = process.env.INBOX_TEND_API_ORIGIN || 'http://127.0.0.1:8765';

export default {
  async rewrites() {
    return [
      { source: '/api/:path*', destination: `${backend}/api/:path*` },
      { source: '/static/:path*', destination: `${backend}/static/:path*` },
      { source: '/LICENSE', destination: `${backend}/LICENSE` },
    ];
  },
};
