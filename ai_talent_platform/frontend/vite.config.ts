import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

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
      proxy.on('proxyReq', (proxyReq: { setHeader: (k: string, v: string) => void }, req: { headers: Record<string, string | undefined> }) => {
        const host = req.headers.host || '';
        if (/\.e2b\.app$/i.test(host)) {
          proxyReq.setHeader('x-forwarded-proto', 'https');
          proxyReq.setHeader('x-forwarded-host', host);
        }
      });
    },
  };
}

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: true,
    proxy: {
      '/api': apiProxyOptions(),
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
