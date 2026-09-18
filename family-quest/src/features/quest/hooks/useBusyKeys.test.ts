/**
 * `useBusyKeys` の単体テスト(#659)。
 *
 * このフックの存在理由は「判定は同期的な ref、表示は state」という二重化そのもの
 * なので、その2点 — ref が再レンダーを待たずに読めること / sync を呼ぶまで keys が
 * 変わらないこと — を固定する。ここが崩れると #101 / #391 の連打対策が効かなくなる。
 */
import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useBusyKeys } from './useBusyKeys';

describe('useBusyKeys', () => {
  it('初期状態は空', () => {
    const { result } = renderHook(() => useBusyKeys<string>());

    expect(result.current.keys).toEqual([]);
    expect(result.current.ref.current.size).toBe(0);
  });

  it('ref は再レンダーを待たずに読み書きできる(連打判定に使う)', () => {
    const { result } = renderHook(() => useBusyKeys<string>());

    // sync を呼ばずに追加しても、その場で has() が true になる。
    // ここが state 経由だと、1回目の更新が反映される前の2回目を通してしまう。
    result.current.ref.current.add('a');

    expect(result.current.ref.current.has('a')).toBe(true);
  });

  it('sync を呼ぶまで表示用の keys は変わらない', () => {
    const { result } = renderHook(() => useBusyKeys<string>());

    act(() => {
      result.current.ref.current.add('a');
    });

    expect(result.current.keys).toEqual([]);
  });

  it('sync を呼ぶと ref の内容が keys へ写る', () => {
    const { result } = renderHook(() => useBusyKeys<string>());

    act(() => {
      result.current.ref.current.add('a');
      result.current.ref.current.add('b');
      result.current.sync();
    });

    expect(result.current.keys).toEqual(['a', 'b']);
  });

  it('複数まとめて追加してから1回の sync でも全件反映される', () => {
    // handleApproveAll が「対象全件を先に ref へ入れてから1回だけ sync」する使い方
    const { result } = renderHook(() => useBusyKeys<number>());

    act(() => {
      for (const id of [1, 2, 3]) result.current.ref.current.add(id);
      result.current.sync();
    });

    expect(result.current.keys).toEqual([1, 2, 3]);
  });

  it('削除も sync で反映される', () => {
    const { result } = renderHook(() => useBusyKeys<string>());

    act(() => {
      result.current.ref.current.add('a');
      result.current.ref.current.add('b');
      result.current.sync();
    });
    act(() => {
      result.current.ref.current.delete('a');
      result.current.sync();
    });

    expect(result.current.keys).toEqual(['b']);
  });

  it('keys は ref とは別の配列で、あとから ref を触っても変わらない', () => {
    // スナップショットを配るので、描画側が掴んだ配列が後から書き換わらない
    const { result } = renderHook(() => useBusyKeys<string>());

    act(() => {
      result.current.ref.current.add('a');
      result.current.sync();
    });
    const snapshot = result.current.keys;

    act(() => {
      result.current.ref.current.add('b');
    });

    expect(snapshot).toEqual(['a']);
  });
});
