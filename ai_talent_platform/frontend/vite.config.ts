import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync } from 'node:fs';

// 代理公共配置：
// - changeOrigin=false：保留浏览器原始 Host，后端 build_invitation_link 依据请求 Host
//   生成候选人链接（否则链接会变成 http://127.0.0.1:8000/invite/...，浏览器打不开）；
// - ws=true：面试间 WebSocket（/api/v1/invites/ws/{token}）走同一代理升级；
// - 沙箱预览域（*.e2b.app）是 HTTPS 入口，但代理转发到后端是 HTTP，
//   显式补 X-Forwarded-Proto=https，保证生成的邀约链接是 https。
function apiProxyOptions() {
  return {
    target: 'http://127.0.0.1:8000',
    changeOrigin: false,
    ws: true,
    configure(proxy: { on: (event: string, fn: (...args: any[]) => void) => void }) {
      proxy.on('proxyReq', (proxyReq: { setHeader: (k: string, v: string) => void }, req: { headers: Record<string, string | undefined>; socket: { encrypted?: boolean } }) => {
        const host = req.headers.host || '';
        if (req.socket.encrypted || /\.e2b\.app$/i.test(host)) {
          proxyReq.setHeader('x-forwarded-proto', 'https');
          proxyReq.setHeader('x-forwarded-host', host);
        }
      });
    },
  };
}

const cert = process.env.VITE_HTTPS_CERT;
const key = process.env.VITE_HTTPS_KEY;
if (Boolean(cert) !== Boolean(key)) throw new Error('请同时配置 VITE_HTTPS_CERT 和 VITE_HTTPS_KEY');

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    https: cert && key ? { cert: readFileSync(cert), key: readFileSync(key) } : undefined,
    allowedHosts: true,
    proxy: {
      '/api': apiProxyOptions(),
      '/exam': apiProxyOptions(),
      '/health': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: {
    chunkSizeWarningLimit: 1600,
    rollupOptions: {
      output: {
        manualChunks: { antd: ['antd', '@ant-design/icons'], charts: ['echarts', 'echarts-for-react'] },
      },
    },
  },
});
