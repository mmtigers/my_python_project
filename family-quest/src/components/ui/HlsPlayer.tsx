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

// #392: 致命的なネットワークエラー(camera_router が ffmpeg 起動待ちで返す 503、
// サーバー再起動中の接続失敗など)は一時的なことが多い。以前は MEDIA_ERROR 以外の
// fatal を即 hls.destroy() していたため、ライブ4分割を常時表示している端末では
// 一時的な 503 でタイルが再マウントまで永久に死んでいた。
// NETWORK_ERROR は指数バックオフで hls.startLoad() を再試行し、上限を超えたら
// 「再試行」ボタン付きのエラー表示に落とす(ボタンで HLS インスタンスを再生成する)。
const NETWORK_RETRY_BASE_MS = 1000;
const NETWORK_RETRY_MAX_MS = 30 * 1000;
const NETWORK_RETRY_MAX_ATTEMPTS = 6;

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
    // #392: 「再試行」ボタンで useEffect を再実行(HLSインスタンスを作り直す)させるためのカウンタ
    const [retryNonce, setRetryNonce] = useState(0);

    useEffect(() => {
        const video = videoRef.current;
        if (!video) return;

        if (onVideoRef) onVideoRef(video);

        // ★新しい streamUrl の読み込み開始時は、前回のエラー表示をリセットする
        setStreamError(false);

        let hls: Hls | undefined;
        // #392: バックオフ再試行の状態。cleanup 後にタイマーが発火しても何もしないよう disposed で守る
        let disposed = false;
        let retryTimer: number | null = null;
        let networkRetryCount = 0;

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
            if (autoPlay) {
                // #443: 以前はvideo.play()の失敗(自動再生ポリシー等)がconsole.errorのみで
                // UI状態に反映されず、ユーザーには無音の一時停止画面が残っていた。
                video.play().catch(e => {
                    console.error("Play failed:", e);
                    setStreamError(true);
                });
            }
        };

        const giveUp = () => {
            hls?.destroy();
            hls = undefined;
            setStreamError(true);
        };

        // #392: NETWORK_ERROR の指数バックオフ再試行(1s, 2s, 4s, 8s, 16s, 30s)。
        // 上限回数を超えたら giveUp して「再試行」ボタンに委ねる。
        const scheduleNetworkRetry = () => {
            if (networkRetryCount >= NETWORK_RETRY_MAX_ATTEMPTS) {
                console.error("HLS network error: retry limit reached, giving up.");
                giveUp();
                return;
            }
            const delay = Math.min(NETWORK_RETRY_BASE_MS * 2 ** networkRetryCount, NETWORK_RETRY_MAX_MS);
            networkRetryCount += 1;
            console.warn(`HLS network error: retrying in ${delay}ms (attempt ${networkRetryCount}/${NETWORK_RETRY_MAX_ATTEMPTS})`);
            retryTimer = window.setTimeout(() => {
                retryTimer = null;
                if (disposed || !hls) return;
                hls.startLoad();
            }, delay);
        };

        if (Hls.isSupported()) {
            const instance = new Hls({
                startPosition: startPosition !== undefined ? startPosition : -1,
            });
            hls = instance;
            instance.loadSource(streamUrl);
            instance.attachMedia(video);
            instance.on(Hls.Events.ERROR, (_event, data) => {
                if (!data.fatal) return;
                console.error("HLS Fatal Error:", data);
                if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
                    scheduleNetworkRetry();
                } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
                    // ★変更: 無限ループを防ぐため、3秒以内の連続エラーは破棄する
                    const now = performance.now();
                    if (now - recoverDecodingErrorDate > 3000) {
                        recoverDecodingErrorDate = now;
                        console.warn("メディアエラー: 回復を試みます...");
                        instance.recoverMediaError();
                    } else {
                        console.error("致命的なメディアエラー: 回復できないためHLSを破棄します。");
                        giveUp();
                    }
                } else {
                    giveUp();
                }
            });

            // #392: セグメントが取れた=ネットワークが回復したので、バックオフ段階をリセットする
            instance.on(Hls.Events.FRAG_LOADED, () => {
                networkRetryCount = 0;
            });

            instance.on(Hls.Events.MANIFEST_PARSED, () => {
                if (autoPlay) {
                    // #443: video.play()の失敗をUI状態に反映する(上のhandleLoadedMetadataと同じ理由)。
                    video.play().catch(e => {
                        console.error("Play failed:", e);
                        setStreamError(true);
                    });
                }
            });
        } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
            video.src = streamUrl;
            video.addEventListener('loadedmetadata', handleLoadedMetadata);
            video.addEventListener('error', handleNativeError);
        } else {
            // #443: hls.js非対応かつブラウザのネイティブHLS再生にも非対応の場合、
            // 以前はどちらの分岐にも入らず、hls変数が未初期化のまま何も起きない
            // (映像もエラー表示も出ない)画面になっていた。明示的にエラー表示を出す。
            console.error("HLS is not supported by hls.js and native HLS playback is unavailable in this browser.");
            setStreamError(true);
        }

        return () => {
            disposed = true;
            if (retryTimer !== null) {
                window.clearTimeout(retryTimer);
                retryTimer = null;
            }
            if (Hls.isSupported()) {
                hls?.destroy();
            } else {
                // hls.js 未使用（Safariネイティブ再生）の場合のみ登録したリスナーなので、
                // 対になるブランチでのみ解除する（毎回の再実行でリスナーが積み上がるのを防ぐ）
                video.removeEventListener('loadedmetadata', handleLoadedMetadata);
                video.removeEventListener('error', handleNativeError);
            }
            // #392(F-L4): アンマウント/差し替え時は呼び出し元(RecordView の videoRefs)に
            // null を通知し、切り離された <video> 要素への参照が残らないようにする
            if (onVideoRef) onVideoRef(null);
        };
    }, [streamUrl, autoPlay, startPosition, onVideoRef, retryNonce]);

    return (
        <div className="relative w-full h-full">
            <video
                ref={videoRef}
                muted={muted}
                controls={controls}
                className="w-full h-full object-contain bg-black"
            />
            {streamError && (
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-black/80 text-white text-sm text-center p-4">
                    <span>映像を取得できませんでした</span>
                    <button
                        type="button"
                        // LiveView のタイル全体に付いた onClick(1台拡大表示)へ伝播させない
                        onClick={(e) => { e.stopPropagation(); setRetryNonce(n => n + 1); }}
                        className="px-4 py-2 min-h-[44px] rounded bg-blue-600 hover:bg-blue-500 font-bold transition-colors"
                    >
                        再試行
                    </button>
                </div>
            )}
        </div>
    );
};

export default HlsPlayer;
