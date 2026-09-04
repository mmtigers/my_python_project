import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ChunkErrorBoundary from './ChunkErrorBoundary';

// #362: SW更新後に旧チャンクが404になり lazy() が throw したとき、白画面ではなく
// 自動再読み込み(または再読み込みボタン付きフォールバック)になることを検証する。

const Thrower = ({ error }: { error: Error }) => {
    throw error;
};

const chunkError = () => new TypeError('Failed to fetch dynamically imported module: https://example/quest/assets/SettingsModal-abc123.js');
const safariChunkError = () => new TypeError('Importing a module script failed.');
const ordinaryError = () => new Error('boom');
const suppressWindowError = (e: ErrorEvent) => e.preventDefault();

describe('ChunkErrorBoundary', () => {
    beforeEach(() => {
        window.sessionStorage.clear();
        // React はエラーバウンダリで捕捉したエラーも console.error に出すため抑止する
        vi.spyOn(console, 'error').mockImplementation(() => {});
        // jsdom は捕捉済みのエラーも window の error イベントとして再送出し
        // 仮想コンソールにスタックトレースを出すため、テストログを汚さないよう握りつぶす
        window.addEventListener('error', suppressWindowError);
    });

    afterEach(() => {
        window.removeEventListener('error', suppressWindowError);
        cleanup();
        vi.restoreAllMocks();
    });

    it('renders children when nothing throws', () => {
        render(<ChunkErrorBoundary reload={vi.fn()}><div>ok</div></ChunkErrorBoundary>);
        expect(screen.getByText('ok')).toBeInTheDocument();
    });

    it('auto-reloads once on a chunk load failure (Chrome wording)', () => {
        const reload = vi.fn();
        render(<ChunkErrorBoundary reload={reload}><Thrower error={chunkError()} /></ChunkErrorBoundary>);
        expect(reload).toHaveBeenCalledTimes(1);
    });

    it('auto-reloads on a chunk load failure (Safari wording)', () => {
        const reload = vi.fn();
        render(<ChunkErrorBoundary reload={reload}><Thrower error={safariChunkError()} /></ChunkErrorBoundary>);
        expect(reload).toHaveBeenCalledTimes(1);
    });

    it('does not auto-reload again within the guard window, but offers a manual reload button', () => {
        const reload = vi.fn();
        render(<ChunkErrorBoundary reload={reload}><Thrower error={chunkError()} /></ChunkErrorBoundary>);
        expect(reload).toHaveBeenCalledTimes(1);
        cleanup();

        // 直前の自動リロードから30秒以内に再び失敗 → ループ防止のため自動リロードしない
        render(<ChunkErrorBoundary reload={reload}><Thrower error={chunkError()} /></ChunkErrorBoundary>);
        expect(reload).toHaveBeenCalledTimes(1);
        expect(screen.getByRole('alert')).toHaveTextContent('画面の更新が必要です');

        fireEvent.click(screen.getByRole('button', { name: '再読み込み' }));
        expect(reload).toHaveBeenCalledTimes(2);
    });

    it('shows a fallback with the error message for non-chunk render errors and does not auto-reload', () => {
        const reload = vi.fn();
        render(<ChunkErrorBoundary reload={reload}><Thrower error={ordinaryError()} /></ChunkErrorBoundary>);
        expect(reload).not.toHaveBeenCalled();
        expect(screen.getByRole('alert')).toHaveTextContent('画面の表示に失敗しました');
        expect(screen.getByRole('alert')).toHaveTextContent('boom');

        fireEvent.click(screen.getByRole('button', { name: '再読み込み' }));
        expect(reload).toHaveBeenCalledTimes(1);
    });
});
