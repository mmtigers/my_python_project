import React, { lazy, Suspense } from 'react'
import ReactDOM from 'react-dom/client'
import { registerSW } from 'virtual:pwa-register'
import App from './App' // 拡張子は省略可能
import './index.css'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from './lib/queryClient'
import { SettingsProvider } from './context/SettingsContext'
import { ToastProvider } from './context/ToastContext'
import ChunkErrorBoundary from './components/ui/ChunkErrorBoundary'
import { isCameraRoute } from './lib/routing'

// CameraDashboard(hls.js含む)は /camera 専用でFamily Quest本体とは同時に使われないため、
// 動的importで別チャンクに分離し、通常のクエスト画面の初回読み込みバンドルから除外する。
const CameraDashboard = lazy(() => import('./features/camera/components/CameraDashboard'))

// #362: PWA Service Worker の更新戦略。
// vite-plugin-pwa の registerType: 'autoUpdate' だけでは、プラグインが注入する
// registerSW.js が register() するのみで、更新チェックはブラウザ任せ(ナビゲーション時
// または24時間ごと)になる。常時表示している Echo Show(キオスク端末)は再読込も
// ナビゲーションもしないため、deploy.sh で dist を更新しても旧バンドルのまま動き続け、
// 2026-09-01 の「旧バンドル×新APIスキーマ」障害と同型の窓が開いていた。
// - 1時間ごとに registration.update() で新しい SW を明示的に取りに行く
// - 新しい SW が有効化(skipWaiting + clientsClaim)された時点(controllerchange)で
//   ページを自動で再読み込みし、新しい index.html/チャンク一式に切り替える
const SW_UPDATE_INTERVAL_MS = 60 * 60 * 1000;

if ('serviceWorker' in navigator) {
  // 初回インストール時(まだ controller が無い状態から clientsClaim で初めて制御下に
  // 入るとき)にも controllerchange は発火するが、これは「更新」ではないので
  // 再読み込みしない。既に controller がある状態からの変化のみを更新として扱う。
  let hadController = !!navigator.serviceWorker.controller;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (!hadController) {
      hadController = true;
      return;
    }
    window.location.reload();
  });
}

registerSW({
  immediate: true,
  onRegisteredSW(_swUrl, registration) {
    if (!registration) return;
    window.setInterval(() => {
      registration.update().catch((e: unknown) => console.warn('SW update check failed:', e));
    }, SW_UPDATE_INTERVAL_MS);
  },
  onRegisterError(error: unknown) {
    console.error('SW registration failed:', error);
  },
});

// getElementById は null を返す可能性があるため、! (Non-null assertion) またはチェックを入れる
const rootElement = document.getElementById('root');

if (!rootElement) {
  throw new Error('Failed to find the root element');
}

// ★追加: URLのパスが '/camera' または '/quest/camera' 等で始まる場合はカメラビューワをレンダリングする
const isCameraView = isCameraRoute(window.location.pathname);

// #362: lazy() チャンクの読み込み失敗(SW更新後の旧チャンク404)で白画面にならないよう、
// ルート直下を ChunkErrorBoundary で包む(チャンク失敗時は自動再読み込み)。
ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <ChunkErrorBoundary>
      <QueryClientProvider client={queryClient}>
        {/* ★変更: URLパスによってマウントするアプリを根元から切り替える */}
        {isCameraView ? (
          <Suspense fallback={null}>
            <CameraDashboard />
          </Suspense>
        ) : (
          <SettingsProvider>
            <ToastProvider>
              <App />
            </ToastProvider>
          </SettingsProvider>
        )}
      </QueryClientProvider>
    </ChunkErrorBoundary>
  </React.StrictMode>,
)
