function dashboardPost(url, resultId, busyText) {
    var box = document.getElementById(resultId);
    box.textContent = busyText;
    fetch(url, { method: "POST", credentials: "same-origin" })
        .then(function (res) {
            return res.json().then(function (data) { return { ok: res.ok, data: data }; });
        })
        .then(function (result) {
            box.textContent = result.ok
                ? (result.data.message || "完了しました")
                : "失敗しました: " + (result.data.detail || "");
        })
        .catch(function (e) {
            box.textContent = "通信エラー: " + e;
        });
}
function dashboardRestart() { dashboardPost("/api/system/restart", "restartResult", "再起動コマンドを送信しています..."); }

var backupPollTimer = null;
function dashboardToast(text, ok) {
    var el = document.createElement("div");
    el.className = "dashboard-toast " + (ok ? "ok" : "ng");
    el.setAttribute("role", "alert");
    el.textContent = text;
    document.body.appendChild(el);
    setTimeout(function () { el.classList.add("hide"); }, 5000);
    setTimeout(function () { if (el.parentNode) { el.parentNode.removeChild(el); } }, 5600);
}
function backupFormatTime(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso || "");
    return m ? (m[2] + "/" + m[3] + " " + m[4] + ":" + m[5]) : "";
}
function backupSetBusy(busy) {
    var btn = document.getElementById("backupBtn");
    btn.disabled = busy;
    btn.textContent = busy ? "⏳ バックアップ中..." : "📦 今すぐバックアップ";
}
function backupShowLatest(info) {
    var box = document.getElementById("backupLatest");
    if (!info) { box.textContent = "最新のバックアップ: 見つかりません"; return; }
    box.textContent = "最新のバックアップ: " + backupFormatTime(info.created_at) + " (" + info.size_mb + "MB)";
}
function backupFetchStatus() {
    return fetch("/api/system/backup/status", { credentials: "same-origin" })
        .then(function (res) { if (!res.ok) { throw new Error("HTTP " + res.status); } return res.json(); });
}
function backupPoll(runId) {
    backupFetchStatus().then(function (st) {
        backupShowLatest(st.latest_backup);
        if (st.running) { backupPollTimer = setTimeout(function () { backupPoll(runId); }, 2000); return; }
        backupSetBusy(false);
        var box = document.getElementById("backupResult");
        var last = st.last_result;
        if (last && (runId === null || last.run_id === runId)) {
            var ok = last.success;
            var text = ok ? "✅ バックアップが完了しました (" + last.size_mb + "MB)" : "❌ バックアップに失敗しました: " + last.message;
            box.textContent = text;
            dashboardToast(text, ok);
        } else {
            box.textContent = "";
        }
    }).catch(function (e) {
        // 一時的な通信エラーでは諦めず、少し待って再確認する(結果はサーバー側に残る)。
        backupPollTimer = setTimeout(function () { backupPoll(runId); }, 4000);
    });
}
function dashboardBackup() {
    var box = document.getElementById("backupResult");
    backupSetBusy(true);
    box.textContent = "バックアップを開始しています...";
    fetch("/api/system/backup", { method: "POST", credentials: "same-origin" })
        .then(function (res) { return res.json().then(function (d) { return { ok: res.ok, data: d }; }); })
        .then(function (r) {
            if (!r.ok) { throw new Error(r.data.detail || "開始できませんでした"); }
            box.textContent = "バックアップ中です。完了までお待ちください...";
            backupPoll(r.data.run_id);
        })
        .catch(function (e) {
            backupSetBusy(false);
            box.textContent = "❌ " + e.message;
            dashboardToast("❌ バックアップを開始できませんでした: " + e.message, false);
        });
}
var deployFailCount = 0;
function deploySetBusy(busy) {
    var btn = document.getElementById("deployBtn");
    var chk = document.getElementById("deployConfirm");
    if (busy) {
        btn.disabled = true;
        btn.textContent = "⏳ 更新中...";
    } else {
        btn.textContent = "⬇️ 最新に更新して再起動";
        btn.disabled = !chk.checked;
    }
}
function deployPoll(runId) {
    var box = document.getElementById("deployResult");
    fetch("/api/system/deploy/status", { credentials: "same-origin" })
        .then(function (res) { if (!res.ok) { throw new Error("HTTP " + res.status); } return res.json(); })
        .then(function (st) {
            deployFailCount = 0;
            if (st.running) {
                box.textContent = "更新中です。完了までお待ちください...";
                setTimeout(function () { deployPoll(runId); }, 3000);
                return;
            }
            deploySetBusy(false);
            var last = st.last_result;
            if (last && (runId === null || last.run_id === runId)) {
                var text = (last.success ? "✅ " : "❌ ") + last.message;
                box.textContent = last.success ? text + " ページを再読み込みすると新しい画面になります。" : text;
                dashboardToast(text, last.success);
            } else {
                box.textContent = "";
            }
        })
        .catch(function () {
            // 再起動中はサーバーが応答しない。諦めず待つ(結果は状態ファイルに残る)。
            deployFailCount += 1;
            if (deployFailCount > 200) {
                deploySetBusy(false);
                box.textContent = "❌ サーバーが長時間応答しません。実機の状態を確認してください。";
                return;
            }
            box.textContent = "サーバーを再起動しています。しばらくお待ちください...";
            setTimeout(function () { deployPoll(runId); }, 4000);
        });
}
function dashboardDeploy() {
    var box = document.getElementById("deployResult");
    deploySetBusy(true);
    box.textContent = "更新を開始しています...";
    fetch("/api/system/deploy", { method: "POST", credentials: "same-origin" })
        .then(function (res) { return res.json().then(function (d) { return { ok: res.ok, data: d }; }); })
        .then(function (r) {
            if (!r.ok) { throw new Error(r.data.detail || "開始できませんでした"); }
            box.textContent = "更新中です。完了までお待ちください...";
            deployPoll(r.data.run_id);
        })
        .catch(function (e) {
            deploySetBusy(false);
            box.textContent = "❌ " + e.message;
            dashboardToast("❌ 更新を開始できませんでした: " + e.message, false);
        });
}
document.addEventListener("DOMContentLoaded", function () {
    // 更新の実行中にページを開き直した場合は、結果を待つ表示に戻す。
    fetch("/api/system/deploy/status", { credentials: "same-origin" })
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (st) { if (st && st.running) { deploySetBusy(true); deployPoll(null); } })
        .catch(function () {});
    // ページを開いた時点で実行中なら、スピナー表示に戻して結果を待つ。
    backupFetchStatus().then(function (st) {
        backupShowLatest(st.latest_backup);
        if (st.running) { backupSetBusy(true); backupPoll(null); }
    }).catch(function () {
        document.getElementById("backupLatest").textContent = "最新のバックアップ: 取得できませんでした";
    });
});
