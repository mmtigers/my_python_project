import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useState } from 'react';
import { useCurrentUser } from './useCurrentUser';
import { User } from '@/types';

// Issue #659 改善案1(c): useCurrentUser は「保存済み user_id の復元」と
// 「範囲外インデックスのクランプ」という #393/#552 の再発防止そのものを担うが、
// カバレッジ0%で回帰検知が効いていなかった。localStorage は実物(jsdom)を使い、
// lib/currentUserStorage.ts のキー名・形状検証もあわせて固定する。

const STORAGE_KEY = 'familyQuest.currentUserId.v1';

const makeUser = (user_id: string, name: string): User => ({
    user_id,
    name,
    level: 1,
    exp: 0,
    gold: 0,
});

const dad = makeUser('dad', 'パパ');
const mom = makeUser('mom', 'ママ');
const son = makeUser('son', 'ともや');
const guest = makeUser('guest', 'ゲスト');

const FAMILY = [dad, mom, son];

// currentUserIdx 自体は App.tsx のトップレベル状態として残す設計(#552)なので、
// テストでも同じ形(useState を持つ呼び出し元)を再現する。
function Harness({ users, initialIdx = 0 }: { users: User[]; initialIdx?: number }) {
    const [idx, setIdx] = useState(initialIdx);
    useCurrentUser(users, idx, setIdx);
    return (
        <div>
            <span data-testid="idx">{idx}</span>
            <button onClick={() => setIdx(1)}>switch-to-1</button>
        </div>
    );
}

const currentIdx = () => Number(screen.getByTestId('idx').textContent);
const stored = () => window.localStorage.getItem(STORAGE_KEY);

describe('useCurrentUser 保存済みuser_idの復元 (#393)', () => {
    beforeEach(() => {
        window.localStorage.clear();
    });

    afterEach(() => {
        cleanup();
        window.localStorage.clear();
    });

    it('実データが揃った時点で、保存済みuser_idに対応するindexへ切り替える', () => {
        window.localStorage.setItem(STORAGE_KEY, 'son');
        render(<Harness users={FAMILY} />);
        expect(currentIdx()).toBe(2);
        // 解決後のレンダーで、そのユーザーが改めて保存される(値は変わらない)
        expect(stored()).toBe('son');
    });

    it('保存済みuser_idが見つからない場合は0番目のままにし、その人を保存し直す', () => {
        // メンバー削除後などに起こる。#393 以前は保存していたindexが範囲外になって
        // 「接続エラー(guest)」カードが出ていた。
        window.localStorage.setItem(STORAGE_KEY, 'ghost');
        render(<Harness users={FAMILY} />);
        expect(currentIdx()).toBe(0);
        expect(stored()).toBe('dad');
    });

    it('保存値が無ければ0番目を保存する', () => {
        render(<Harness users={FAMILY} />);
        expect(currentIdx()).toBe(0);
        expect(stored()).toBe('dad');
    });

    it('空文字列の保存値は無視する(形状検証)', () => {
        window.localStorage.setItem(STORAGE_KEY, '');
        render(<Harness users={FAMILY} />);
        expect(currentIdx()).toBe(0);
        expect(stored()).toBe('dad');
    });

    it('users が INITIAL_USERS フォールバック(1人)の間は解決も保存もしない', () => {
        // 実データ到着前の一瞬で正しい保存値を消してしまわないためのガード。
        window.localStorage.setItem(STORAGE_KEY, 'son');
        render(<Harness users={[guest]} />);
        expect(currentIdx()).toBe(0);
        expect(stored()).toBe('son');
    });

    it('ユーザー切替のたびに選択中のuser_idを保存する', () => {
        render(<Harness users={FAMILY} />);
        expect(stored()).toBe('dad');
        fireEvent.click(screen.getByText('switch-to-1'));
        expect(currentIdx()).toBe(1);
        expect(stored()).toBe('mom');
    });
});

describe('useCurrentUser 範囲外indexのクランプ', () => {
    beforeEach(() => {
        window.localStorage.clear();
    });

    afterEach(() => {
        cleanup();
        window.localStorage.clear();
    });

    it('currentUserIdx が users の範囲外なら0番目へクランプする', () => {
        render(<Harness users={FAMILY} initialIdx={5} />);
        expect(currentIdx()).toBe(0);
        expect(stored()).toBe('dad');
    });

    it('境界値: index === users.length はクランプ対象', () => {
        render(<Harness users={FAMILY} initialIdx={3} />);
        expect(currentIdx()).toBe(0);
    });

    it('境界値: index === users.length - 1 はクランプしない', () => {
        render(<Harness users={FAMILY} initialIdx={2} />);
        expect(currentIdx()).toBe(2);
        expect(stored()).toBe('son');
    });

    it('保存済みuser_idの解決はクランプより優先される', () => {
        window.localStorage.setItem(STORAGE_KEY, 'mom');
        render(<Harness users={FAMILY} initialIdx={99} />);
        expect(currentIdx()).toBe(1);
        expect(stored()).toBe('mom');
    });
});
