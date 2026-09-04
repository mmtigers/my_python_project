import { useCallback, useEffect, useRef, useState } from 'react';

interface UseLongPressOptions {
    onLongPress: () => void;
    // 長押しに達しなかった場合の通常タップ。渡さなければ短タップは何もしない。
    onShortTap?: () => void;
    thresholdMs?: number;
    disabled?: boolean;
    // #389: 長押し発火後、この時間内に届いた click を「直前の長押しの余韻」とみなして
    // 呼び出し側が無視できるようにするための猶予時間。
    clickSuppressMs?: number;
}

interface UseLongPressResult {
    // 押し始めてからの経過割合(0〜1)。長押し中のプログレス表示に使う
    pressProgress: number;
    isPressing: boolean;
    // #389: 直近 clickSuppressMs 以内に onLongPress が発火したかどうか。
    // 長押し(取消)→取消APIの応答→再取得で同じDOMノードに onClick(完了確認) が
    // 付け替わった直後、指を離した瞬間の click が「完了確認モーダル」を開いてしまう
    // 競合を、呼び出し側の onClick ハンドラでガードするために使う。
    wasFiredRecently: () => boolean;
    handlers: {
        onPointerDown: (e: React.PointerEvent) => void;
        onPointerUp: (e: React.PointerEvent) => void;
        onPointerLeave: (e: React.PointerEvent) => void;
        onPointerCancel: (e: React.PointerEvent) => void;
    };
}

const PROGRESS_TICK_MS = 30;
const DEFAULT_CLICK_SUPPRESS_MS = 400;

// 完了済み/申請中クエストの「取り消し」を、うっかりタップで発火させないための
// 長押しジェスチャー用フック。閾値に達したら onLongPress、
// 達する前に指を離したら onShortTap を呼ぶ。
export function useLongPress({
    onLongPress,
    onShortTap,
    thresholdMs = 600,
    disabled = false,
    clickSuppressMs = DEFAULT_CLICK_SUPPRESS_MS,
}: UseLongPressOptions): UseLongPressResult {
    const timeoutRef = useRef<number | null>(null);
    const intervalRef = useRef<number | null>(null);
    const firedRef = useRef(false);
    // #389: 直近の長押し発火時刻。未発火は null。
    const lastFiredAtRef = useRef<number | null>(null);
    const [pressProgress, setPressProgress] = useState(0);
    const [isPressing, setIsPressing] = useState(false);

    const clearTimers = useCallback(() => {
        if (timeoutRef.current !== null) {
            window.clearTimeout(timeoutRef.current);
            timeoutRef.current = null;
        }
        if (intervalRef.current !== null) {
            window.clearInterval(intervalRef.current);
            intervalRef.current = null;
        }
    }, []);

    const onPointerDown = useCallback((e: React.PointerEvent) => {
        if (disabled) return;
        e.stopPropagation();
        firedRef.current = false;
        setIsPressing(true);
        setPressProgress(0);

        const startedAt = Date.now();
        intervalRef.current = window.setInterval(() => {
            setPressProgress(Math.min(1, (Date.now() - startedAt) / thresholdMs));
        }, PROGRESS_TICK_MS);

        timeoutRef.current = window.setTimeout(() => {
            firedRef.current = true;
            lastFiredAtRef.current = Date.now();
            clearTimers();
            setIsPressing(false);
            setPressProgress(0);
            onLongPress();
        }, thresholdMs);
    }, [disabled, onLongPress, thresholdMs, clearTimers]);

    const endPress = useCallback((triggerShortTap: boolean) => {
        clearTimers();
        setIsPressing(false);
        setPressProgress(0);
        if (triggerShortTap && !firedRef.current && onShortTap) {
            onShortTap();
        }
    }, [clearTimers, onShortTap]);

    useEffect(() => clearTimers, [clearTimers]);

    const wasFiredRecently = useCallback((): boolean => {
        const firedAt = lastFiredAtRef.current;
        return firedAt !== null && Date.now() - firedAt < clickSuppressMs;
    }, [clickSuppressMs]);

    const onPointerUp = useCallback((e: React.PointerEvent) => {
        e.stopPropagation();
        endPress(true);
    }, [endPress]);

    const onPointerLeave = useCallback(() => {
        endPress(false);
    }, [endPress]);

    const onPointerCancel = useCallback(() => {
        endPress(false);
    }, [endPress]);

    return {
        pressProgress,
        isPressing,
        wasFiredRecently,
        handlers: { onPointerDown, onPointerUp, onPointerLeave, onPointerCancel },
    };
}
