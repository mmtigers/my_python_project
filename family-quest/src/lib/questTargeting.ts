// family-quest/src/lib/questTargeting.ts
//
// #412(品質): クエストの target_user 判定（'all' / 'siblings' / 個別 user_id 一致）は
// 以前 QuestList.tsx（一覧のフィルタ）と FamilyDashboard.tsx（「今日やることが無いか」の
// 判定）に、ほぼ同一のロジックが重複して実装されていた。ここに集約し、両者から参照する。
//
// #371 (Q-M3/F-M5): 'role_' プレフィックスのターゲット判定は、サーバー側の完了API
// (services/quest_service.py の _process_complete_quest_locked)が 'all'/本人/'siblings'
// 以外を無条件403で拒否するため、'role_*' ターゲットのクエストは一覧には表示されても
// 誰も完了できないという不整合な潜在バグだった(quest_data.pyに実際の'role_*'
// ターゲットが存在しないため顕在化していなかった)。オーナー判断(role_*ターゲットは
// 今後も使わない)により削除した。
import { Quest, User } from '@/types';

export function isQuestVisibleToUser(quest: Quest, user: User): boolean {
    const target = quest.target_user;
    if (!target || target === 'all') return true;

    if (target === 'siblings') {
        // 兄妹連携クエスト: 対象は子ども(role_child)全員
        return user.role === 'role_child';
    }
    return target === user.user_id;
}
