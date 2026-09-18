import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  // #650: deploy.sh は dist.next へビルドして rename で差し替え、直前の成果物を dist.prev に残す。
  // eslint は .gitignore ではなく flat config のこの一覧を見るため、ここに足さないと
  // 差し替え途中・失敗後(=まさに原因調査で lint を叩きたい場面)に minified JS を走査してしまう。
  globalIgnores(['dist', 'dist.next', 'dist.prev', 'coverage']),
  {
    files: ['**/*.{js,jsx,ts,tsx}'],
    extends: [
      js.configs.recommended,
      ...tseslint.configs.recommended,
      reactHooks.configs['recommended-latest'],
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    rules: {
      // Disable the base rule in favor of the TS-aware variant (recommended by
      // typescript-eslint) to avoid duplicate/incorrect reports on TS syntax.
      'no-unused-vars': 'off',
      '@typescript-eslint/no-unused-vars': ['error', { varsIgnorePattern: '^[A-Z_]', argsIgnorePattern: '^_' }],
    },
  },
  {
    // main.tsx はアプリのエントリポイント(ReactDOM.createRoot でツリーを起動するだけで
    // 何もexportしない)ため、Fast Refresh の対象になることが原理的に無い。
    // eslint-plugin-react-refresh 0.5 でルールが厳格化され、lazy() の戻り値も
    // 「コンポーネント定義」として数えるようになった結果、
    // `const CameraDashboard = lazy(() => import(...))` が
    // 「exportが無いファイルにコンポーネントがある」として報告されるようになった。
    // このlazy importは /camera 専用チャンクを本体バンドルから分離するためのもので
    // (main.tsx 冒頭のコメント参照)、別ファイルへ移すと分離の意図が読み取りにくくなる。
    // エントリポイントだけをこのルールの対象外にする。
    files: ['src/main.tsx'],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
])
