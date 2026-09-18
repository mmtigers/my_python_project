import { createContext } from 'react';
import type { CompletedSignal, ID } from '@/types';

// QuestActivityContext.tsx(Provider本体)と useQuestActivity.ts(フック)の両方から
// 参照する型・Context object をここに集約する。
// (react-refresh の「1ファイルはコンポーネントのみexportする」制約により、
//  コンポーネントを export する QuestActivityContext.tsx と分離している。
//  context/settingsShared.ts と同じ構成。)

/**
 * 「いま画面上で進行中のクエスト操作」を表す横断的なUI状態(#659)。
 *
 * いずれも App.tsx が唯一の持ち主で、表示側(QuestList / ApprovalList)は
 * 読むだけである。以前は App → FamilyDashboard → FamilyPanel → QuestList と
 * 素通しの props で運んでいた。中継する2つのコンポーネントは値を一切
 * 使わないのに型と引数だけを持たされていたため、Context へ移した。
 */
export interface QuestActivityValue {
    /**
     * #102: 完了APIが実際に成功した時点でのみ、対象クエストの完了音・無限クエストの
     * クールダウンを発火させるための通知。
     * #363: `userId` を含み、各パネルの QuestItem は**自分のユーザーの完了にのみ**
     * 反応する(横画面では4枚のパネルが同じ signal を受け取るため)。
     */
    completedSignal: CompletedSignal | null;
    /** #391: 完了/取消APIが送信中の `(user_id, quest_id)` キー集合。 */
    processingQuestKeys: string[];
    /** #391(F-L8): 承認APIが送信中の履歴id集合。 */
    busyHistoryIds: ID[];
}

export const QuestActivityContext = createContext<QuestActivityValue | null>(null);

/**
 * Provider が無い場合に使う既定値。
 *
 * 「何も進行中でない」は表示上まったく無害(ローディングもクールダウンも出ない)
 * なので、throw ではなくこれを返す。表示専用コンポーネント(QuestList /
 * ApprovalList)を Provider 無しで単体テストできる状態を保つため。
 */
export const QUEST_ACTIVITY_IDLE: QuestActivityValue = {
    completedSignal: null,
    processingQuestKeys: [],
    busyHistoryIds: [],
};
