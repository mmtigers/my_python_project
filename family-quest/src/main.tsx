import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App' // 拡張子は省略可能
import './index.css'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from './lib/queryClient'

// getElementById は null を返す可能性があるため、! (Non-null assertion) またはチェックを入れる
const rootElement = document.getElementById('root');

if (!rootElement) {
  throw new Error('Failed to find the root element');
}

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
)