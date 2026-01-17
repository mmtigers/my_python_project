import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import path from 'path'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(),
  
  // ▼▼▼ PWA Plugin 設定追加 ▼▼▼
  VitePWA({
    registerType: 'autoUpdate', // ユーザーへの通知なしに自動更新（シンプル構成）
    includeAssets: ['favicon.ico', 'apple-touch-icon.png', 'vite.svg'],
    manifest: {
      name: 'Family Quest',
      short_name: 'Quest',
      description: '家族で楽しむタスク管理RPG',
      theme_color: '#ffffff',
      background_color: '#ffffff',
      display: 'standalone',
      scope: '/quest/',
      start_url: '/quest/',
      icons: [
        {
          src: 'pwa-192x192.png',
          sizes: '192x192',
          type: 'image/png'
        },
        {
          src: 'pwa-512x512.png',
          sizes: '512x512',
          type: 'image/png'
        }
      ]
    }
  })
  ],
  base: '/quest/',
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: '0.0.0.0',
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/uploads': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      }
    }
  }
})