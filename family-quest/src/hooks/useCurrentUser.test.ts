/**
 * Issue #659 改善案1(c): `useCurrentUser` の localStorage 復元と範囲外クランプ。
 *
 * このフックは #393 の事故(保存していた index が範囲外になり「接続エラー(guest)」
 * カードが出る)への対策そのもので、カバレッジ 0% のまま残っていた。
 * 復元・クランプ・保存の3つと、実データ到着前は何もしないという待機条件を固定する。
 */
// @vitest-environment jsdom
import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { User } from '@/types';
import { useCurrentUser } from './useCurrentUser';

const KEY = 'familyQuest.currentUserId.v1';

const user = (id: string): User =>
  ({ user_id: id, name: id, role: 'role_child', level: 1, exp: 0, gold: 0 }) as User;

const FAMILY = [user('dad'), user('mom'), user('son'), user('daughter')];
// 実データ到着前のフォールバック(INITIAL_USERS 相当)。1人しかいない。
const GUEST_ONLY = [user('guest')];

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

/** users と currentUserIdx を与えてフックを1回動かし、setCurrentUserIdx の呼び出しを返す。 */
function run(users: User[], currentUserIdx: number) {
  const setCurrentUserIdx = vi.fn();
  renderHook(() => useCurrentUser(users, currentUserIdx, setCurrentUserIdx));
  return setCurrentUserIdx;
}

describe('useCurrentUser: 保存済みユーザーの復元', () => {
  it('保存されたuser_idの位置へ切り替える', () => {
    window.localStorage.setItem(KEY, 'son');

    const set = run(FAMILY, 0);

    expect(set).toHaveBeenCalledWith(2);
  });

  it('保存値が見つからなければ切り替えず、現在の選択をそのまま保存する', () => {
    // メンバー削除後などに起きる。#393: ここで0番目へ倒す挙動は
    // 「範囲外のとき」だけで、見つからないだけなら現在値を維持する。
    window.localStorage.setItem(KEY, 'grandpa');

    const set = run(FAMILY, 1);

    expect(set).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(KEY)).toBe('mom');
  });

  it('保存値が無ければ現在の選択を保存する', () => {
    const set = run(FAMILY, 3);

    expect(set).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(KEY)).toBe('daughter');
  });
});

describe('useCurrentUser: 範囲外のクランプ', () => {
  it('indexがusers.length以上なら0番目へ戻す', () => {
    const set = run(FAMILY, 9);

    expect(set).toHaveBeenCalledWith(0);
  });

  it('クランプするときは保存しない(不正なindexの人を保存しない)', () => {
    const set = run(FAMILY, 9);

    expect(set).toHaveBeenCalledWith(0);
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });

  it('末尾ちょうどのindexは範囲内なので触らない', () => {
    const set = run(FAMILY, 3);

    expect(set).not.toHaveBeenCalled();
  });
});

describe('useCurrentUser: 実データ到着前は何もしない', () => {
  it('usersが1人だけの間は解決も保存もしない', () => {
    // INITIAL_USERS のフォールバック(guest 1人)を保存してしまうと、
    // 起動のたびに実データ到着前の一瞬で正しい保存値を消してしまう。
    window.localStorage.setItem(KEY, 'son');

    const set = run(GUEST_ONLY, 0);

    expect(set).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(KEY)).toBe('son');
  });

  it('usersが空でも落ちず、何もしない', () => {
    const set = run([], 0);

    expect(set).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });

  it('1人だけの間は範囲外indexでもクランプしない', () => {
    const set = run(GUEST_ONLY, 5);

    expect(set).not.toHaveBeenCalled();
  });
});

describe('useCurrentUser: localStorageが使えない環境', () => {
  it('読めなくても落ちず、現在の選択で動く', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });

    const set = run(FAMILY, 1);

    expect(set).not.toHaveBeenCalled();
  });

  it('書けなくても落ちない(切替操作自体は続行できる)', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });

    expect(() => run(FAMILY, 1)).not.toThrow();
  });
});
