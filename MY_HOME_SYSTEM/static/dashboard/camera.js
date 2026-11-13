// 見守りページのライブ映像。ふだんはタップしたカメラだけ再生する(同時に再生するのは1台)。
// 「全部まとめて見る」を押したときだけ全台を同時に再生し、もう一度押す(「すべて止める」)と全台止まる。
// 状態は各タイル(.camera-tile)の data-state に持ち、表示の切り替えはCSSが行う:
//   idle(再生前。「▶ ライブを見る」)/ loading(接続中)/ playing(再生中)/ error(接続できません)
(function () {
    // この時間内に再生が始まらなければ「接続できません」にする(サーバー側はffmpegの起動を最大5秒待つ)。
    var START_TIMEOUT_MS = 20000;
    // 全台同時再生中か(「全部まとめて見る」で true、「すべて止める」・画面を離れたときに false)。
    var allMode = false;
    var startingAll = false;
    var tiles = [];
    var allButton = null;

    function isRunning(tile) {
        return tile.dataset.state === "loading" || tile.dataset.state === "playing";
    }

    function updateAllButton() {
        if (!allButton) { return; }
        allButton.textContent = allMode ? "■ すべて止める" : "▶ 全部まとめて見る";
        allButton.setAttribute("aria-pressed", allMode ? "true" : "false");
    }

    function release(tile) {
        if (tile._timer) { clearTimeout(tile._timer); tile._timer = null; }
        if (tile._hls) { tile._hls.destroy(); tile._hls = null; }
        var video = tile.querySelector("video");
        if (tile._native) {
            tile._native = false;
            video.removeAttribute("src");
            video.load();
        }
    }

    // 動いているカメラが1台も無くなったら、全台同時再生を終わりにする。
    // 全台が接続に失敗したあとボタンが「すべて止める」のまま残り、そこから1台ずつ始めても
    // 前のカメラが止まらない、という状態を防ぐ。
    function leaveAllModeIfNothingRuns() {
        // 全台の再生を順に始めている最中は判定しない(1台目が即座に失敗しても、残りを同時再生として始める)。
        if (allMode && !startingAll && !tiles.some(isRunning)) {
            allMode = false;
            updateAllButton();
        }
    }

    function stop(tile) {
        release(tile);
        tile.dataset.state = "idle";
        tile.querySelector("video").controls = false;
        leaveAllModeIfNothingRuns();
    }

    function stopAll() {
        allMode = false;
        tiles.forEach(function (tile) { if (tile.dataset.state !== "idle") { stop(tile); } });
        updateAllButton();
    }

    function startAll() {
        allMode = true;
        startingAll = true;
        tiles.forEach(function (tile) { if (!isRunning(tile)) { start(tile); } });
        startingAll = false;
        updateAllButton();
        leaveAllModeIfNothingRuns();
    }

    function fail(tile, message) {
        release(tile);
        tile.querySelector("video").controls = false;
        tile.dataset.state = "error";
        var text = tile.querySelector(".camera-error-text");
        if (text) { text.textContent = message || "接続できません"; }
        leaveAllModeIfNothingRuns();
    }

    function start(tile) {
        // 1台ずつ見ているときは、ほかのカメラを止めてから始める(全台同時再生中は止めない)。
        if (!allMode) {
            tiles.forEach(function (other) { if (other !== tile && other.dataset.state !== "idle") { stop(other); } });
        }
        release(tile);
        tile.dataset.state = "loading";
        var video = tile.querySelector("video");
        var url = "/api/cameras/live/" + encodeURIComponent(tile.dataset.cameraId) + "/stream.m3u8";
        tile._timer = setTimeout(function () {
            if (tile.dataset.state === "loading") { fail(tile); }
        }, START_TIMEOUT_MS);

        if (window.Hls && window.Hls.isSupported()) {
            var hls = new window.Hls();
            tile._hls = hls;
            hls.on(window.Hls.Events.ERROR, function (event, data) {
                // 致命的でないエラーは hls.js が自分で再試行する
                if (data && data.fatal) { fail(tile); }
            });
            hls.on(window.Hls.Events.MANIFEST_PARSED, function () {
                video.play().catch(function () {});
            });
            hls.loadSource(url);
            hls.attachMedia(video);
        } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
            tile._native = true;
            video.src = url;
            video.play().catch(function () {});
        } else {
            fail(tile, "この端末ではライブ映像を再生できません");
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        tiles = Array.prototype.slice.call(document.querySelectorAll(".camera-tile"));
        if (!tiles.length) { return; }

        allButton = document.getElementById("cameraAllBtn");
        if (allButton) {
            allButton.addEventListener("click", function () {
                if (allMode) { stopAll(); } else { startAll(); }
            });
            updateAllButton();
        }

        tiles.forEach(function (tile) {
            var video = tile.querySelector("video");
            video.addEventListener("playing", function () {
                if (tile.dataset.state !== "loading") { return; }
                if (tile._timer) { clearTimeout(tile._timer); tile._timer = null; }
                tile.dataset.state = "playing";
                // 再生が始まるまでは操作部品を出さない(「▶ ライブを見る」の下に重なって見えるため)
                video.controls = true;
            });
            video.addEventListener("error", function () {
                // hls.js 利用時のエラーは hls.js のイベントで受ける
                if (tile._native) { fail(tile); }
            });
            tile.querySelector(".camera-play").addEventListener("click", function () { start(tile); });
            tile.querySelector(".camera-retry").addEventListener("click", function () { start(tile); });
        });

        // 画面を離れたら止める(サーバー側は視聴が途絶えるとカメラへの接続を閉じる)。
        document.addEventListener("visibilitychange", function () {
            if (document.hidden) { stopAll(); }
        });

        // ホームのカメラの写真・「🚗 駐車場」カードは(?camera=<id>)でこのページを開く。
        // 指定されたカメラを先頭に移して、そのカメラだけ再生を始める。
        var requested = null;
        try { requested = new URLSearchParams(window.location.search).get("camera"); } catch (e) {}
        if (!requested) { return; }
        tiles.forEach(function (tile) {
            if (tile.dataset.cameraId !== requested || !tile.parentNode) { return; }
            tile.classList.add("camera-tile-requested");
            tile.parentNode.insertBefore(tile, tile.parentNode.firstChild);
            start(tile);
        });
    });
})();
