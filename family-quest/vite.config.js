// family-quest/vite.config.js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  base: '/quest/',
  server: {
    host: '0.0.0.0', // スマホなど他の端末から見れるようにする
    proxy: {
      '/api': { // /api で始まる通信をPythonサーバーへ転送
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      }
    }
  }
})