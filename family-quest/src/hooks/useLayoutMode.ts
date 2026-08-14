import { useEffect, useState } from 'react';

// Echo Show 15 (常設・横画面) 想定の閾値。実機での見え方を見て調整可。
const LANDSCAPE_QUERY = '(min-width: 900px) and (orientation: landscape)';

export type LayoutMode = 'landscape' | 'portrait';

const getInitialMode = (): LayoutMode => {
    if (typeof window === 'undefined' || !window.matchMedia) return 'portrait';
    return window.matchMedia(LANDSCAPE_QUERY).matches ? 'landscape' : 'portrait';
};

// 横画面(Echo Show 15等の常設デバイス)/縦画面(スマホ)のレイアウト判定フック。
// window.matchMedia の変化を購読し、リサイズ・回転にリアルタイムに追従する。
export function useLayoutMode(): LayoutMode {
    const [mode, setMode] = useState<LayoutMode>(getInitialMode);

    useEffect(() => {
        if (typeof window === 'undefined' || !window.matchMedia) return;

        const mql = window.matchMedia(LANDSCAPE_QUERY);
        const handleChange = () => setMode(mql.matches ? 'landscape' : 'portrait');

        handleChange();

        // Safari 13以前は addEventListener 非対応のため addListener にフォールバックする
        if (mql.addEventListener) {
            mql.addEventListener('change', handleChange);
            return () => mql.removeEventListener('change', handleChange);
        } else {
            mql.addListener(handleChange);
            return () => mql.removeListener(handleChange);
        }
    }, []);

    return mode;
}
