import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  base: '/quest/',
  server: {
    host: '0.0.0.0',
    proxy: {
      // APIリクエストを転送
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      // ★追加: アップロード画像のアクセスも転送
      '/uploads': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      }
    }
  }
})