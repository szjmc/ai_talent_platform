import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import basicSsl from '@vitejs/plugin-basic-ssl';

// getUserMedia（面试间摄像头/麦克风）只在安全上下文可用：HTTPS 或 localhost。
// 用局域网 IP（如 http://172.30.48.1:5173）访问时 navigator.mediaDevices 为 undefined。
// 设置 VITE_DEV_HTTPS=1 启动即可获得自签名 HTTPS（首次访问需在浏览器信任证书）：
//   Windows: set VITE_DEV_HTTPS=1 && npm run dev   |   *nix: VITE_DEV_HTTPS=1 npm run dev
const useHttps = process.env.VITE_DEV_HTTPS === '1';

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
  plugins: [react(), ...(useHttps ? [basicSsl()] : [])],
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: true,
    ...(useHttps ? { https: {} } : {}),
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
