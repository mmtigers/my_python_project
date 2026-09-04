import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { SettingsProvider } from './SettingsContext';
import { useSettings } from './useSettings';
import { SETTINGS_STORAGE_KEY } from './settingsShared';

// #412(F-L7): localStorage に保存された設定の形状が壊れていても(他タブの旧バージョンの
// 書き込み・手動編集等)、SettingsProvider の初期化がクラッシュせず、DEFAULT_SETTINGS
// 相当の安全な値にフォールバックすることを検証する。以前は iconFirstUserIds が
// 配列以外だと App.tsx の `iconFirstUserIds.includes(...)` で例外になっていた。

const Probe = () => {
    const { density, iconFirstUserIds, userThemeColors } = useSettings();
    return (
        <div>
            <span data-testid="density">{density}</span>
            <span data-testid="iconFirstUserIds">{JSON.stringify(iconFirstUserIds)}</span>
            <span data-testid="userThemeColors">{JSON.stringify(userThemeColors)}</span>
            {/* App.tsx と同じ使い方: 配列でなければここで例外になる */}
            <span data-testid="includesCheck">{String(iconFirstUserIds.includes('son'))}</span>
        </div>
    );
};

const renderProbe = () => render(<SettingsProvider><Probe /></SettingsProvider>);

describe('SettingsContext localStorage shape validation (#412 F-L7)', () => {
    afterEach(() => {
        cleanup();
        window.localStorage.clear();
    });

    it('falls back to defaults when nothing is stored', () => {
        renderProbe();
        expect(screen.getByTestId('density')).toHaveTextContent('comfortable');
        expect(screen.getByTestId('iconFirstUserIds')).toHaveTextContent('[]');
    });

    it('loads a well-formed saved value as-is', () => {
        window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify({
            density: 'compact',
            iconFirstUserIds: ['son', 'daughter'],
            userThemeColors: { son: 'blue' },
        }));
        renderProbe();
        expect(screen.getByTestId('density')).toHaveTextContent('compact');
        expect(screen.getByTestId('iconFirstUserIds')).toHaveTextContent('["son","daughter"]');
        expect(screen.getByTestId('userThemeColors')).toHaveTextContent('{"son":"blue"}');
    });

    it('does not crash and falls back when iconFirstUserIds is not an array', () => {
        window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify({
            density: 'compact',
            iconFirstUserIds: 'son', // 壊れた形状: 文字列
        }));
        expect(() => renderProbe()).not.toThrow();
        expect(screen.getByTestId('iconFirstUserIds')).toHaveTextContent('[]');
        expect(screen.getByTestId('includesCheck')).toHaveTextContent('false');
        // 壊れていないフィールド(density)は活かす
        expect(screen.getByTestId('density')).toHaveTextContent('compact');
    });

    it('drops non-string entries inside iconFirstUserIds', () => {
        window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify({
            iconFirstUserIds: ['son', 42, null, { bad: true }, 'daughter'],
        }));
        renderProbe();
        expect(screen.getByTestId('iconFirstUserIds')).toHaveTextContent('["son","daughter"]');
    });

    it('falls back to the default density for an unknown value', () => {
        window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify({ density: 'huge' }));
        renderProbe();
        expect(screen.getByTestId('density')).toHaveTextContent('comfortable');
    });

    it('drops unknown theme color keys and non-object userThemeColors', () => {
        window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify({
            userThemeColors: { son: 'blue', daughter: 'ultraviolet' },
        }));
        renderProbe();
        expect(screen.getByTestId('userThemeColors')).toHaveTextContent('{"son":"blue"}');
        cleanup();

        window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify({ userThemeColors: 'blue' }));
        renderProbe();
        expect(screen.getByTestId('userThemeColors')).toHaveTextContent('{}');
    });

    it('falls back to defaults for malformed JSON', () => {
        window.localStorage.setItem(SETTINGS_STORAGE_KEY, '{not valid json');
        expect(() => renderProbe()).not.toThrow();
        expect(screen.getByTestId('density')).toHaveTextContent('comfortable');
    });

    it('falls back to defaults when the stored value is a JSON array (not an object)', () => {
        window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(['son', 'daughter']));
        expect(() => renderProbe()).not.toThrow();
        expect(screen.getByTestId('iconFirstUserIds')).toHaveTextContent('[]');
    });
});
