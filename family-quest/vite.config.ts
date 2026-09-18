import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import path from 'path'
import { fileURLToPath } from 'url'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(),
  
  // ▼▼▼ PWA Plugin 設定追加 ▼▼▼
  VitePWA({
    useCredentials: true,
    registerType: 'autoUpdate', // ユーザーへの通知なしに自動更新（シンプル構成）
    includeAssets: ['favicon.ico', 'apple-touch-icon.png', 'vite.svg'],
    workbox: {
      // #660: 既定の globPatterns は js/css/html/ico/png/svg のみで、効果音の mp3
      // (public/*.mp3、計6ファイル 約190KB)がプリキャッシュから漏れていた。
      // オフライン・初回タップ時に音が鳴らない/遅れる原因になるため明示的に含める。
      globPatterns: ['**/*.{js,css,html,ico,png,svg,mp3}'],
    },
    manifest: {
      // #660: 既定では manifest.lang が "en" になるが、UI もコンテンツも日本語のため ja とする
      // (index.html の <html lang="ja"> とも揃う)。
      lang: 'ja',
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
      // Issue #539: ESM 設定ファイルでは __dirname は非推奨(Vite 8 で警告)のため import.meta.url から解決する
      '@': path.resolve(fileURLToPath(new URL('.', import.meta.url)), './src'),
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
  },
  build: {
    rollupOptions: {
      output: {
        // 変更頻度の低いベンダーライブラリをアプリコードと別チャンクに分離する。
        // hls.js はカメラ機能(/camera)専用で重いため、main.tsx側のdynamic importと
        // あわせて通常のFamily Quest画面のバンドルから完全に除外する。
        manualChunks: {
          'vendor-react': ['react', 'react-dom'],
          'vendor-motion': ['framer-motion'],
          'vendor-query': ['@tanstack/react-query'],
        },
      },
    },
  },
})