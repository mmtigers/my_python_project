// MY_HOME_SYSTEM/static/dashboard/*.js(ダッシュボードの外部JS。Issue #949)用のESLint設定。
// 実機のビルド・起動にnpmを要求しないよう、実行は開発・CI(frontendジョブ)に限り、
// ESLint本体とプラグインはfamily-questの`node_modules`を借りる(依存の追加なし)。
// ESLintは設定ファイルのあるディレクトリの外のファイルを無視するため、設定はリポジトリ直下に置く。
// 実行: `cd family-quest && npm run lint:dashboard`
const { createRequire } = require('node:module')
const req = createRequire(require('node:path').join(__dirname, 'family-quest', 'package.json'))
const js = req('@eslint/js')
const globals = req('globals')
const { defineConfig } = req('eslint/config')

module.exports = defineConfig([
  {
    files: ['MY_HOME_SYSTEM/static/dashboard/*.js'],
    extends: [js.configs.recommended],
    languageOptions: {
      ecmaVersion: 2020,
      // ESMではない素のスクリプト。`onclick`属性から呼ぶ関数はグローバルに置く前提。
      sourceType: 'script',
      globals: { ...globals.browser, Hls: 'readonly' },
    },
    rules: {
      // localStorage等が使えない環境を黙って無視する`catch (e) {}`は意図的。
      'no-empty': ['error', { allowEmptyCatch: true }],
      // HTMLの`onclick="dashboardRestart()"`等から呼ぶ関数は、そのファイル内では未使用に見える。
      'no-unused-vars': ['error', { vars: 'local', args: 'none', caughtErrors: 'none' }],
    },
  },
])
