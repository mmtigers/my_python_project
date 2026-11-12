// 全ページ共通: 表示テーマ(自動/暗い/明るい)の切り替えと、オフライン画面用のService Workerの登録。
// 保存済みテーマの適用そのものは、最初の描画より前に行う必要があるためページ側のインラインにある。
(function () {
    var KEY = "dashTheme";
    var ORDER = ["auto", "dark", "light"];
    var LABELS = { auto: "自動", dark: "暗い", light: "明るい" };
    // このスクリプトは defer で読まれるので、実行時に currentScript が取れる。
    var base = (document.currentScript && document.currentScript.dataset.base) || "/dashboard";

    function currentTheme() {
        var t = document.documentElement.getAttribute("data-theme");
        return t === "dark" || t === "light" ? t : "auto";
    }

    function applyTheme(theme) {
        if (theme === "auto") {
            document.documentElement.removeAttribute("data-theme");
        } else {
            document.documentElement.setAttribute("data-theme", theme);
        }
        try {
            if (theme === "auto") { window.localStorage.removeItem(KEY); } else { window.localStorage.setItem(KEY, theme); }
        } catch (e) {}
        var label = document.getElementById("themeToggleLabel");
        if (label) { label.textContent = LABELS[theme]; }
    }

    function init() {
        var button = document.getElementById("themeToggle");
        if (button) {
            applyTheme(currentTheme());
            button.addEventListener("click", function () {
                applyTheme(ORDER[(ORDER.indexOf(currentTheme()) + 1) % ORDER.length]);
            });
        }
        openTargetDetails();
        window.addEventListener("hashchange", openTargetDetails);
    }

    // 折りたたみ(<details>)の中の場所へ #id で飛んだときは、開いてから見せる。
    function openTargetDetails() {
        var id = window.location.hash ? window.location.hash.slice(1) : "";
        var target = id ? document.getElementById(id) : null;
        var opened = false;
        for (var node = target; node; node = node.parentElement) {
            if (node.tagName === "DETAILS" && !node.open) { node.open = true; opened = true; }
        }
        if (opened && target) { target.scrollIntoView(); }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

    // サーバーに繋がらないとき、ブラウザのエラー画面の代わりに案内を出す(sw.js)。
    if ("serviceWorker" in navigator) {
        navigator.serviceWorker.register(base + "/sw.js", { scope: base + "/" }).catch(function () {});
    }
})();
