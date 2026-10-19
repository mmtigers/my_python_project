(function () {
    function el(tag, text, cls) {
        var node = document.createElement(tag);
        if (cls) { node.className = cls; }
        node.textContent = text;
        return node;
    }
    function fmtBoot(iso) {
        return iso ? iso.slice(5, 10).replace("-", "/") + " " + iso.slice(11, 16) : "";
    }
    function commitText(c) { return c.sha + " " + c.subject + (c.date ? " (" + c.date + ")" : ""); }

    function badge(text, cls) { return el("span", text, "badge " + cls); }
    function card(label, c, sub) {
        var node = document.createElement("div");
        node.className = "git-card";
        node.appendChild(el("p", label, "git-card-label"));
        node.appendChild(el("p", c, "git-card-main"));
        if (sub) { node.appendChild(el("p", sub, "git-card-sub")); }
        return node;
    }

    function render(d) {
        var box = document.getElementById("gitStatusBody");
        if (!box) { return; }
        box.textContent = "";
        if (!d.ok) { box.appendChild(el("p", d.error || "取得できませんでした", "git-status-line git-status-warn")); return; }

        // 上部の状態バッジ(重要度の高い順)
        var badges = document.createElement("div");
        badges.className = "status-badges";
        if (d.pending_restart) { badges.appendChild(badge("⚠️ 未再起動", "badge-warn")); }
        if (d.dirty === true) { badges.appendChild(badge("⚠️ 未コミットの変更 " + d.dirty_count + "件", "badge-warn")); }
        else if (d.dirty === false) { badges.appendChild(badge("✅ 変更なし", "badge-ok")); }
        if (d.behind > 0) { badges.appendChild(badge("⬇️ 未取り込み " + d.behind + "件", "badge-warn")); }
        else if (d.behind === 0) { badges.appendChild(badge("✅ 最新", "badge-ok")); }
        if (d.ahead > 0) { badges.appendChild(badge("手元だけ " + d.ahead + "件", "badge-info")); }
        if (badges.childNodes.length) { box.appendChild(badges); }

        // 3段(起動中 / 手元 / GitHub)
        var trio = document.createElement("div");
        trio.className = "git-trio";
        if (d.running) {
            trio.appendChild(card("▶ 起動中のコード", d.running.sha + " " + d.running.subject, "起動 " + fmtBoot(d.running.booted_at)));
        } else {
            trio.appendChild(card("▶ 起動中のコード", "記録なし", "この機能の導入後の最初の起動から分かります"));
        }
        trio.appendChild(card("📁 手元(実機)の最新", commitText(d.head), d.branch ? "ブランチ " + d.branch : ""));
        if (d.remote) { trio.appendChild(card("☁️ GitHubの最新", commitText(d.remote), "")); }
        box.appendChild(trio);

        if (d.pending_restart) {
            box.appendChild(el("p", "取り込み済みですが、まだ再起動されていません(起動中のコードは古いままです)", "git-status-line git-status-warn"));
        }
        if (d.error) { box.appendChild(el("p", d.error, "git-status-line git-status-warn")); }
        if (d.recent && d.recent.length) {
            box.appendChild(el("p", "最近のコミット", "meta"));
            var list = document.createElement("ul");
            list.className = "git-commit-list";
            d.recent.forEach(function (c) {
                var li = document.createElement("li");
                li.appendChild(el("span", c.running ? "▶ 起動中" : (c.pulled ? "✅ 取り込み済み" : "⬇️ 未取り込み"), "git-commit-badge"));
                li.appendChild(document.createTextNode(" "));
                li.appendChild(el("span", c.sha, "git-commit-sha"));
                li.appendChild(document.createTextNode(" " + c.subject + (c.date ? " (" + c.date + ")" : "")));
                list.appendChild(li);
            });
            box.appendChild(list);
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        fetch("/api/system/git/status", { credentials: "same-origin" })
            .then(function (res) { return res.json(); })
            .then(render)
            .catch(function () {
                var box = document.getElementById("gitStatusBody");
                if (box) { box.textContent = "Gitの状態を取得できませんでした"; }
            });
    });
})();
