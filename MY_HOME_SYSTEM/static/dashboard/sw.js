// ダッシュボードのService Worker。役割は1つだけ:
// ページの読み込み(ナビゲーション)でサーバーに繋がらなかったとき、ブラウザのエラー画面の代わりに
// 「接続できません」の案内を返す。ページやデータはキャッシュしない(古い家の状態を見せないため)。
// ナビゲーション以外(CSS・JS・画像・API・映像)には一切関与しない。
var OFFLINE_HTML = [
    "<!DOCTYPE html>",
    '<html lang="ja"><head><meta charset="utf-8">',
    '<meta name="viewport" content="width=device-width, initial-scale=1">',
    "<title>接続できません - おうちの様子</title>",
    "<style>",
    ":root { color-scheme: light dark; }",
    "body { margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;",
    "  font-family: 'Helvetica Neue', Arial, 'Hiragino Sans', Meiryo, sans-serif; background: #f3f5f9; color: #1a2230; }",
    "main { max-width: 420px; padding: 24px; text-align: center; }",
    "h1 { font-size: 1.3rem; margin: 0 0 12px; }",
    "p { font-size: 0.95rem; line-height: 1.6; margin: 0 0 12px; color: #5b6678; }",
    "button { min-height: 48px; padding: 0 24px; border: 0; border-radius: 12px; font-size: 1rem; font-weight: bold;",
    "  background: #2457d6; color: #fff; }",
    "@media (prefers-color-scheme: dark) { body { background: #0e1319; color: #e6ebf2; } p { color: #9aa7b8; }",
    "  button { background: #8ab4ff; color: #0e1319; } }",
    "</style></head><body><main>",
    "<h1>⚠️ おうちのサーバーに接続できません</h1>",
    "<p>サーバーの再起動中か、ネットワークが切れている可能性があります。少し待ってから、もう一度お試しください。</p>",
    '<p id="at"></p>',
    '<button type="button" onclick="location.reload()">もう一度読み込む</button>',
    "</main><" + "script>",
    "var d = new Date();",
    "document.getElementById('at').textContent = '確認した時刻: ' + d.getHours() + ':' + ('0' + d.getMinutes()).slice(-2);",
    "</" + "script></body></html>",
].join("\n");

self.addEventListener("install", function () {
    self.skipWaiting();
});

self.addEventListener("activate", function (event) {
    event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", function (event) {
    if (event.request.mode !== "navigate") { return; }
    event.respondWith(
        fetch(event.request).catch(function () {
            return new Response(OFFLINE_HTML, {
                status: 503,
                headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
            });
        })
    );
});
