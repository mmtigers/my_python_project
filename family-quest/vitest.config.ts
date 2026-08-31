import { defineConfig, mergeConfig } from 'vitest/config'
import viteConfig from './vite.config.ts'

// vite.config.ts に `test` フィールドを直接足すと、defineConfig の
// オーバーロード解決が壊れ build.rollupOptions.output.manualChunks の型が
// 通らなくなる(vitest/config の defineConfig を使っても、vite の
// defineConfig のまま型注釈だけ足しても同様)。tsc -b(通常ビルド)には
// vitest 由来の型を一切持ち込みたくないため、テスト設定はこのファイルに分離する。
export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.ts'],
    },
  })
)
