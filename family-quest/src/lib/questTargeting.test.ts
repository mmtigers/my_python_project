import { describe, expect, it } from 'vitest';
import { isQuestVisibleToUser } from './questTargeting';
import { Quest, User } from '@/types';

// #412(品質): QuestList.tsx / FamilyDashboard.tsx で重複していた target_user 判定を
// 集約した純粋関数の単体テスト。

const child: User = { user_id: 'son', name: 'ともや', level: 1, exp: 0, gold: 0, role: 'role_child' };
const otherChild: User = { user_id: 'daughter', name: 'ゆい', level: 1, exp: 0, gold: 0, role: 'role_child' };
const adult: User = { user_id: 'dad', name: 'まさひろ', level: 1, exp: 0, gold: 0, role: 'role_adult' };

const quest = (target?: string): Quest => ({ quest_id: 1, title: 'テスト', target_user: target });

describe('isQuestVisibleToUser', () => {
    it('is visible to everyone when target_user is missing or "all"', () => {
        expect(isQuestVisibleToUser(quest(undefined), child)).toBe(true);
        expect(isQuestVisibleToUser(quest('all'), adult)).toBe(true);
    });

    it('"siblings" is visible only to role_child users', () => {
        expect(isQuestVisibleToUser(quest('siblings'), child)).toBe(true);
        expect(isQuestVisibleToUser(quest('siblings'), otherChild)).toBe(true);
        expect(isQuestVisibleToUser(quest('siblings'), adult)).toBe(false);
    });

    it('a plain target_user matches only that exact user_id (#371: "role_" prefix targeting was removed, unused/never-created)', () => {
        expect(isQuestVisibleToUser(quest('son'), child)).toBe(true);
        expect(isQuestVisibleToUser(quest('son'), otherChild)).toBe(false);

        // #371: 'role_'プレフィックスは特別扱いせず、他の値と同様にuser_idの完全一致でのみ判定する
        // (バックエンドの完了APIが'role_*'ターゲットを常に403で拒否するため、表示側で
        // 特別扱いすると「一覧には出るが完了できない」不整合になる)。
        expect(isQuestVisibleToUser(quest('role_adult'), adult)).toBe(false);
        expect(isQuestVisibleToUser(quest('role_child'), child)).toBe(false);
    });
});
