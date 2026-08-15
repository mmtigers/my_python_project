import React, { lazy, Suspense } from 'react'
import ReactDOM from 'react-dom/client'
import App from './App' // 拡張子は省略可能
import './index.css'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from './lib/queryClient'
import { SettingsProvider } from './context/SettingsContext'
import { ToastProvider } from './context/ToastContext'

// CameraDashboard(hls.js含む)は /camera 専用でFamily Quest本体とは同時に使われないため、
// 動的importで別チャンクに分離し、通常のクエスト画面の初回読み込みバンドルから除外する。
const CameraDashboard = lazy(() => import('./features/camera/components/CameraDashboard'))

// getElementById は null を返す可能性があるため、! (Non-null assertion) またはチェックを入れる
const rootElement = document.getElementById('root');

if (!rootElement) {
  throw new Error('Failed to find the root element');
}

// ★追加: URLのパスが '/camera' または '/quest/camera' 等で始まる場合はカメラビューワをレンダリングする
const isCameraView = window.location.pathname.includes('/camera');

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
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
  </React.StrictMode>,
)