(function () {
    function startStream(video) {
        var url = "/api/cameras/live/" + encodeURIComponent(video.dataset.cameraId) + "/stream.m3u8";
        if (window.Hls && window.Hls.isSupported()) {
            var hls = new window.Hls();
            hls.loadSource(url);
            hls.attachMedia(video);
            hls.on(window.Hls.Events.MANIFEST_PARSED, function () {
                video.play().catch(function () {});
            });
        } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
            video.src = url;
            video.addEventListener("loadedmetadata", function () {
                video.play().catch(function () {});
            });
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        var videos = document.querySelectorAll(".camera-video");
        if (!videos.length) { return; }

        // ホームページの「🚗 駐車場」カードは(?camera=<id>)でこのページを開く。
        // 全台を同時に表示するので、指定されたカメラのタイルを先頭に移して目立たせる。
        var requested = null;
        try { requested = new URLSearchParams(window.location.search).get("camera"); } catch (e) {}
        Array.prototype.forEach.call(videos, function (video) {
            if (requested && video.dataset.cameraId === requested) {
                var tile = video.closest(".camera-tile");
                if (tile && tile.parentNode) {
                    tile.classList.add("camera-tile-requested");
                    tile.parentNode.insertBefore(tile, tile.parentNode.firstChild);
                }
            }
        });
        // 先頭へ移したあとの並びで全台のストリームを開始する。
        document.querySelectorAll(".camera-video").forEach(startStream);
    });
})();
