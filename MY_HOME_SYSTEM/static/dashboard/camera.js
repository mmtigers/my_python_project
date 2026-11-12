// 見守りページのライブ映像。タップしたカメラだけ再生する(同時に再生するのは1台)。
// 状態は各タイル(.camera-tile)の data-state に持ち、表示の切り替えはCSSが行う:
//   idle(再生前。「▶ ライブを見る」)/ loading(接続中)/ playing(再生中)/ error(接続できません)
(function () {
    // この時間内に再生が始まらなければ「接続できません」にする(サーバー側はffmpegの起動を最大5秒待つ)。
    var START_TIMEOUT_MS = 20000;
    var active = null;

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

    function stop(tile) {
        release(tile);
        tile.dataset.state = "idle";
        tile.querySelector("video").controls = false;
        if (active === tile) { active = null; }
    }

    function fail(tile, message) {
        release(tile);
        tile.querySelector("video").controls = false;
        tile.dataset.state = "error";
        var text = tile.querySelector(".camera-error-text");
        if (text) { text.textContent = message || "接続できません"; }
        if (active === tile) { active = null; }
    }

    function start(tile) {
        if (active && active !== tile) { stop(active); }
        release(tile);
        active = tile;
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
        var tiles = document.querySelectorAll(".camera-tile");
        if (!tiles.length) { return; }

        Array.prototype.forEach.call(tiles, function (tile) {
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
            if (document.hidden && active) { stop(active); }
        });

        // ホームのカメラの写真・「🚗 駐車場」カードは(?camera=<id>)でこのページを開く。
        // 指定されたカメラを先頭に移して、そのカメラだけ再生を始める。
        var requested = null;
        try { requested = new URLSearchParams(window.location.search).get("camera"); } catch (e) {}
        if (!requested) { return; }
        Array.prototype.forEach.call(tiles, function (tile) {
            if (tile.dataset.cameraId !== requested || !tile.parentNode) { return; }
            tile.classList.add("camera-tile-requested");
            tile.parentNode.insertBefore(tile, tile.parentNode.firstChild);
            start(tile);
        });
    });
})();
