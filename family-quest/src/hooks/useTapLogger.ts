import { useEffect, useRef } from 'react';
import { createTapLogger, type TapContext } from '../lib/tapLogger';

// 画面タップログ(lib/tapLogger.ts)を、マウント中だけ有効にする。
// 文脈(選択中ユーザー・画面・向き)は ref に入れ替え、リスナー側はタップ時点の最新値を
// 読む。これにより画面が切り替わってもリスナーを張り直さない。
export function useTapLogger(context: TapContext): void {
    const contextRef = useRef<TapContext>(context);
    // レンダー中の ref 書き換えは避け、描画のたびに effect で最新へ入れ替える。
    useEffect(() => {
        contextRef.current = context;
    });

    useEffect(() => {
        const logger = createTapLogger(() => contextRef.current);
        return logger.start();
    }, []);
}
