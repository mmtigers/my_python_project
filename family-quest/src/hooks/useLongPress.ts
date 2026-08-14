import { useCallback, useRef, useState } from 'react';

interface UseLongPressOptions {
    onLongPress: () => void;
    // 長押しに達しなかった場合の通常タップ。渡さなければ短タップは何もしない。
    onShortTap?: () => void;
    thresholdMs?: number;
    disabled?: boolean;
}

interface UseLongPressResult {
    // 押し始めてからの経過割合(0〜1)。長押し中のプログレス表示に使う
    pressProgress: number;
    isPressing: boolean;
    handlers: {
        onPointerDown: (e: React.PointerEvent) => void;
        onPointerUp: (e: React.PointerEvent) => void;
        onPointerLeave: (e: React.PointerEvent) => void;
        onPointerCancel: (e: React.PointerEvent) => void;
    };
}

const PROGRESS_TICK_MS = 30;

// 完了済み/申請中クエストの「取り消し」を、うっかりタップで発火させないための
// 長押しジェスチャー用フック。閾値に達したら onLongPress、
// 達する前に指を離したら onShortTap を呼ぶ。
export function useLongPress({
    onLongPress,
    onShortTap,
    thresholdMs = 600,
    disabled = false,
}: UseLongPressOptions): UseLongPressResult {
    const timeoutRef = useRef<number | null>(null);
    const intervalRef = useRef<number | null>(null);
    const firedRef = useRef(false);
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
        handlers: { onPointerDown, onPointerUp, onPointerLeave, onPointerCancel },
    };
}
