import { useContext } from 'react';
import { QUEST_ACTIVITY_IDLE, QuestActivityContext, QuestActivityValue } from './questActivityShared';

/**
 * 進行中のクエスト操作(完了通知・送信中キー・承認中id)を読む。
 *
 * Provider が無い場合は「何も進行中でない」既定値を返す(`useSettings` と違って
 * throw しない)。表示専用コンポーネントの単体テストを Provider 無しで書ける
 * 状態を保つため。
 */
export function useQuestActivity(): QuestActivityValue {
    return useContext(QuestActivityContext) ?? QUEST_ACTIVITY_IDLE;
}
