import { defineConfig, mergeConfig } from 'vitest/config'
import viteConfig from './vite.config'

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
      coverage: {
        provider: 'v8',
        // json-summary は CI のカバレッジラチェット(.github/scripts/check_coverage_ratchet.py)が読む
        reporter: ['text', 'html', 'lcov', 'json-summary'],
        // Issue #495: 計測対象は src/ の実装のみ。テスト・型定義・セットアップは
        // 除外する(バックエンドの .coveragerc と同じ「本当に実行不能なものだけ
        // 除外」方針)。
        include: ['src/**/*.{ts,tsx}'],
        exclude: ['src/**/*.test.{ts,tsx}', 'src/test/**', 'src/**/*.d.ts'],
        // Issue #495 では「まず可視化に徹する」として閾値を置かなかった。その後
        // master 比 0.5pt のラチェットが入ったが、ラチェットは `pull_request` でしか
        // 走らず、比較元の成果物(保持14日)が取れないときはスキップされる。つまり
        // フロントエンドだけ「何のゲートも効かない状態」が起こりうるため、
        // バックエンド(--cov-fail-under)・DDD と同じく固定の床を設ける。
        // 値は 2026-09-21 の実測(lines 78.64 / statements 75.65 / branches 63.73 /
        // functions 64.70)の約3pt下。実測が伸びたら段階的に引き上げること。
        thresholds: {
          lines: 75,
          statements: 72,
          branches: 60,
          functions: 61,
        },
      },
    },
  })
)
