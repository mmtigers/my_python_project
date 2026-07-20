import React, { useEffect, useRef } from 'react';
import Hls from 'hls.js';

interface HlsPlayerProps {
    streamUrl: string;
    muted?: boolean;
    autoPlay?: boolean;
    controls?: boolean;
    startPosition?: number;
    onVideoRef?: (element: HTMLVideoElement | null) => void;
}

const HlsPlayer: React.FC<HlsPlayerProps> = ({
    streamUrl,
    muted = true,
    autoPlay = true,
    controls = false,
    startPosition,
    onVideoRef
}) => {
    const videoRef = useRef<HTMLVideoElement>(null);

    useEffect(() => {
        const video = videoRef.current;
        if (!video) return;

        if (onVideoRef) onVideoRef(video);

        let hls: Hls;

        // ★追加: 無限ループ防止のための時間記録用変数
        let recoverDecodingErrorDate = 0;

        if (Hls.isSupported()) {
            hls = new Hls({
                startPosition: startPosition !== undefined ? startPosition : -1,
            });
            hls.loadSource(streamUrl);
            hls.attachMedia(video);
            hls.on(Hls.Events.ERROR, (event, data) => {
                if (data.fatal) {
                    console.error("HLS Fatal Error:", data);
                    if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
                        // ★変更: 無限ループを防ぐため、3秒以内の連続エラーは破棄する
                        const now = performance.now();
                        if (now - recoverDecodingErrorDate > 3000) {
                            recoverDecodingErrorDate = now;
                            console.warn("メディアエラー: 回復を試みます...");
                            hls.recoverMediaError();
                        } else {
                            console.error("致命的なメディアエラー: 回復できないためHLSを破棄します。");
                            hls.destroy();
                        }
                    } else {
                        hls.destroy();
                    }
                }
            });


            hls.on(Hls.Events.MANIFEST_PARSED, () => {
                if (autoPlay) video.play().catch(e => console.error("Play failed:", e));
            });
        } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
            video.src = streamUrl;
            video.addEventListener('loadedmetadata', () => {
                if (startPosition) video.currentTime = startPosition;
                if (autoPlay) video.play().catch(e => console.error("Play failed:", e));
            });
        }

        return () => {
            if (hls) hls.destroy();
        };
    }, [streamUrl, autoPlay, startPosition]);

    return (
        <video
            ref={videoRef}
            muted={muted}
            controls={controls}
            className="w-full h-full object-contain bg-black"
        />
    );
};

export default HlsPlayer;