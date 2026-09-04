// family-quest/src/lib/questTargeting.ts
//
// #412(品質): クエストの target_user 判定（'all' / 'siblings' / 'role_' プレフィックス /
// 個別 user_id 一致）は以前 QuestList.tsx（一覧のフィルタ）と FamilyDashboard.tsx
// （「今日やることが無いか」の判定）に、ほぼ同一のロジックが重複して実装されていた。
// ここに集約し、両者から参照する。
import { Quest, User } from '@/types';

export function isQuestVisibleToUser(quest: Quest, user: User): boolean {
    const target = quest.target_user;
    if (!target || target === 'all') return true;

    if (target === 'siblings') {
        // 兄妹連携クエスト: 対象は子ども(role_child)全員
        return user.role === 'role_child';
    }
    if (target.startsWith('role_')) {
        return user.role === target;
    }
    return target === user.user_id;
}
