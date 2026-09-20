import { describe, expect, it } from 'vitest';
import { queryClient } from './queryClient';

// #803: バックグラウンドに回したタブへ戻ると、実機で1時間半前の残高が表示された。
// モバイルのバックグラウンドタブは refetchInterval のタイマーごと凍結されるため、
// refetchOnWindowFocus を false にしていると復帰時の再取得の契機が無くなる。
// 「理由の記録が無いまま既定値を上書きする」ことで起きた回帰なので、
// 既定オプションの値そのものをここで固定する。

describe('queryClient の既定オプション (#803)', () => {
    const defaults = queryClient.getDefaultOptions().queries;

    it('フォーカス復帰時に再取得する (バックグラウンド復帰で古いデータを表示しない)', () => {
        expect(defaults?.refetchOnWindowFocus).toBe(true);
    });

    it('staleTime が有限であり、フォーカス復帰のたびに無条件で再取得はしない', () => {
        expect(defaults?.staleTime).toBe(1000 * 60);
    });

    it('失敗時のリトライは1回', () => {
        expect(defaults?.retry).toBe(1);
    });
});
