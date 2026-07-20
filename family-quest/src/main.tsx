import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App' // 拡張子は省略可能
import CameraDashboard from './features/camera/components/CameraDashboard' // ★追加
import './index.css'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from './lib/queryClient'

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
      {isCameraView ? <CameraDashboard /> : <App />}
    </QueryClientProvider>
  </React.StrictMode>,
)