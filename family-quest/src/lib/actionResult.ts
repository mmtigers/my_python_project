import { Reward } from '@/types';

// useGameData.ts の completeQuest/cancelQuest/buyReward/rejectQuest
// ラッパー関数群の戻り値をまとめて受け取るための型（各関数は success 以外のフィールドが少しずつ異なる）
export interface ActionResult {
    success: boolean;
    status?: string;
    message?: string;
    earnedMedals?: number;
    leveledUp?: boolean;
    newGold?: number;
    reward?: Reward;
    reason?: string;
    detail?: string;
}

export const ERROR_REASON_MESSAGES: { [key: string]: string } = {
    gold: "お金が足りません！",
    pending: "すでに申請中です",
    permission: "権限がありません",
    error: "エラーが発生しました",
};

// バックエンドが具体的なエラー内容(detail)を返している場合はそれを優先表示する
export const resolveErrorText = (res: ActionResult, fallback: string): string =>
    res.detail || (res.reason && ERROR_REASON_MESSAGES[res.reason]) || fallback;
