import React, { useEffect, useRef, useState } from 'react';
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
    // ★追加: HLS致命的エラー/ネイティブ再生エラー発生時にユーザーへ視覚的に知らせるためのフラグ
    const [streamError, setStreamError] = useState(false);

    useEffect(() => {
        const video = videoRef.current;
        if (!video) return;

        if (onVideoRef) onVideoRef(video);

        // ★新しい streamUrl の読み込み開始時は、前回のエラー表示をリセットする
        setStreamError(false);

        let hls: Hls;

        // ★追加: 無限ループ防止のための時間記録用変数
        let recoverDecodingErrorDate = 0;

        // Safari用: ネイティブ再生時のエラーハンドラ（cleanup で解除できるよう名前付き関数にする）
        const handleNativeError = () => {
            console.error("Native video playback error (Safari HLS)");
            setStreamError(true);
        };

        // Safari用: メタデータ読み込み完了時のハンドラ（cleanup で removeEventListener するため名前付き関数にする）
        const handleLoadedMetadata = () => {
            if (startPosition) video.currentTime = startPosition;
            if (autoPlay) video.play().catch(e => console.error("Play failed:", e));
        };

        if (Hls.isSupported()) {
            hls = new Hls({
                startPosition: startPosition !== undefined ? startPosition : -1,
            });
            hls.loadSource(streamUrl);
            hls.attachMedia(video);
            hls.on(Hls.Events.ERROR, (_event, data) => {
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
                            setStreamError(true);
                        }
                    } else {
                        hls.destroy();
                        setStreamError(true);
                    }
                }
            });


            hls.on(Hls.Events.MANIFEST_PARSED, () => {
                if (autoPlay) video.play().catch(e => console.error("Play failed:", e));
            });
        } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
            video.src = streamUrl;
            video.addEventListener('loadedmetadata', handleLoadedMetadata);
            video.addEventListener('error', handleNativeError);
        }

        return () => {
            if (hls) {
                hls.destroy();
            } else {
                // hls.js 未使用（Safariネイティブ再生）の場合のみ登録したリスナーなので、
                // 対になるブランチでのみ解除する（毎回の再実行でリスナーが積み上がるのを防ぐ）
                video.removeEventListener('loadedmetadata', handleLoadedMetadata);
                video.removeEventListener('error', handleNativeError);
            }
        };
    }, [streamUrl, autoPlay, startPosition, onVideoRef]);

    return (
        <div className="relative w-full h-full">
            <video
                ref={videoRef}
                muted={muted}
                controls={controls}
                className="w-full h-full object-contain bg-black"
            />
            {streamError && (
                <div className="absolute inset-0 flex items-center justify-center bg-black/80 text-white text-sm text-center p-4 pointer-events-none">
                    映像を取得できませんでした
                </div>
            )}
        </div>
    );
};

export default HlsPlayer;
