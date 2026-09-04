// family-quest/src/lib/errorDetail.ts
//
// apiClient.ts がスローする Error の message には、バックエンドが返す
// {"detail": "..."} の内容(または "API Error: <status>" / 通信エラーの文言)が
// 入っている。呼び出し元(App.tsx のエラーモーダル、InventoryList のトースト、
// CameraDashboard のバナー)がユーザーに実際のエラー内容を表示できるよう、
// unknown 型の例外から表示用文字列を取り出す処理をここに集約する
// (#412: 以前は useGameData.ts / InventoryList.tsx / CameraDashboard.tsx に
// ほぼ同じ関数が3重複していた)。
import { ZodError } from 'zod';

// Error なら message(空文字は無視)、それ以外は fallback を返す。
// fallback を省略した場合は undefined を返す(呼び出し側で reason 別の既定文言に
// フォールバックする App.tsx の resolveErrorText 向け)。
export function extractErrorDetail(error: unknown): string | undefined;
export function extractErrorDetail(error: unknown, fallback: string): string;
export function extractErrorDetail(error: unknown, fallback?: string): string | undefined {
    if (error instanceof Error && error.message) return error.message;
    return fallback;
}

// #390: /api/quest/data の取得失敗をユーザー向けバナーに表示するための文言。
// Zod のスキーマ検証エラーは message が JSON 配列の生ダンプで読めないため、
// 最初の不一致箇所(パス + 理由)だけを短く要約する。それ以外は
// extractErrorDetail に委ねる。
export function describeGameDataError(error: unknown, fallback: string): string {
    if (error instanceof ZodError) {
        const first = error.issues[0];
        const where = first?.path.length ? first.path.join('.') : '(root)';
        const why = first?.message ?? 'unknown';
        return `サーバー応答の形式が想定と異なります (${where}: ${why})`;
    }
    return extractErrorDetail(error, fallback);
}
