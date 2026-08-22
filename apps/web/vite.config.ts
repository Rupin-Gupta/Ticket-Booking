import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Proxy in dev so the browser only ever talks to one origin — no CORS
    // preflight, and VITE_API_URL stays empty locally.
    proxy: {
      '/api': { target: 'http://localhost:4000', changeOrigin: true },
      '/health': { target: 'http://localhost:4000', changeOrigin: true },
      // ws: true — without it the upgrade request is proxied as plain HTTP and
      // Socket.IO silently falls back to long-polling forever.
      '/socket.io': { target: 'http://localhost:4000', changeOrigin: true, ws: true },
    },
  },
});
