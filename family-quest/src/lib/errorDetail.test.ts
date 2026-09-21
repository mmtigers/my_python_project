import { describe, expect, it } from 'vitest';
import { z } from 'zod';
import { describeGameDataError, extractErrorDetail } from './errorDetail';

describe('extractErrorDetail', () => {
    it('Error の message をそのまま返す(バックエンドの detail が入っている)', () => {
        expect(extractErrorDetail(new Error('クエストが見つかりません'), '既定')).toBe(
            'クエストが見つかりません',
        );
    });

    it('message が空の Error は fallback に倒す', () => {
        // apiClient が message 無しで throw した場合に空のバナーを出さないための分岐。
        expect(extractErrorDetail(new Error(''), '既定')).toBe('既定');
    });

    it('Error 以外は fallback に倒す', () => {
        expect(extractErrorDetail('文字列', '既定')).toBe('既定');
        expect(extractErrorDetail(null, '既定')).toBe('既定');
        expect(extractErrorDetail({ detail: 'オブジェクト' }, '既定')).toBe('既定');
    });

    it('fallback を省略すると undefined(App.tsx が reason 別の既定文言へ倒すため)', () => {
        expect(extractErrorDetail('文字列')).toBeUndefined();
        expect(extractErrorDetail(new Error('あり'))).toBe('あり');
    });
});

describe('describeGameDataError', () => {
    // #390: ZodError の message は JSON 配列の生ダンプでユーザーには読めない。
    // 最初の不一致箇所だけを短く要約することを固定する。
    it('ZodError は最初の不一致のパスと理由に要約する', () => {
        const schema = z.object({ users: z.array(z.object({ gold: z.number() })) });
        const result = schema.safeParse({ users: [{ gold: '100' }] });
        expect(result.success).toBe(false);

        const text = describeGameDataError(result.error, '既定');
        expect(text).toContain('サーバー応答の形式が想定と異なります');
        expect(text).toContain('users.0.gold');
        // 生の JSON ダンプをそのまま出していないこと
        expect(text).not.toContain('"code"');
    });

    it('パスが空の ZodError は (root) と表示する', () => {
        const result = z.string().safeParse(123);
        expect(result.success).toBe(false);
        expect(describeGameDataError(result.error, '既定')).toContain('(root)');
    });

    it('ZodError 以外は extractErrorDetail と同じ扱い', () => {
        expect(describeGameDataError(new Error('500 Internal'), '既定')).toBe('500 Internal');
        expect(describeGameDataError(undefined, '既定')).toBe('既定');
    });
});
