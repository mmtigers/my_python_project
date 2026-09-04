import React, { useEffect, useMemo, useState } from 'react';
import {
    SettingsContext, SettingsState, SettingsContextValue, Density, ThemeColorKey,
    DEFAULT_SETTINGS, SETTINGS_STORAGE_KEY, THEME_COLORS,
} from './settingsShared';

// アプリ全体の表示設定。localStorage に永続化し、次回起動時も引き継ぐ。
// バックエンドに保存する必要のない「この端末でのUI好み」のみを扱う。
// 型・定数・useSettings フックは settingsShared.ts / useSettings.ts に分離している
// (react-refresh の「1ファイルはコンポーネントのみexportする」制約のため)。

// #412(F-L7): localStorage の値は他タブでの旧バージョン書き込み・手動編集・
// ブラウザ拡張機能等により、型定義と食い違う形状になりうる。以前は
// `{ ...DEFAULT_SETTINGS, ...parsed }` で単純にマージしていたため、例えば
// iconFirstUserIds が配列以外の値(文字列/オブジェクト等)だと、そのまま
// state に乗ってしまい、App.tsx の `iconFirstUserIds.includes(...)` で
// 例外が発生しアプリ全体がクラッシュしていた。フィールドごとに形状を検証し、
// 不正な値は DEFAULT_SETTINGS の対応する値にフォールバックする。

const VALID_DENSITIES: Density[] = ['comfortable', 'compact'];

function isValidDensity(value: unknown): value is Density {
    return typeof value === 'string' && (VALID_DENSITIES as string[]).includes(value);
}

function sanitizeIconFirstUserIds(value: unknown): string[] {
    if (!Array.isArray(value)) return DEFAULT_SETTINGS.iconFirstUserIds;
    return value.filter((id): id is string => typeof id === 'string');
}

const VALID_THEME_COLOR_KEYS: string[] = THEME_COLORS.map(c => c.key);

function sanitizeUserThemeColors(value: unknown): Record<string, ThemeColorKey> {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        return { ...DEFAULT_SETTINGS.userThemeColors };
    }
    const result: Record<string, ThemeColorKey> = {};
    for (const [userId, color] of Object.entries(value as Record<string, unknown>)) {
        if (typeof color === 'string' && VALID_THEME_COLOR_KEYS.includes(color)) {
            result[userId] = color as ThemeColorKey;
        }
    }
    return result;
}

function loadSettings(): SettingsState {
    if (typeof window === 'undefined') return DEFAULT_SETTINGS;
    try {
        const raw = window.localStorage.getItem(SETTINGS_STORAGE_KEY);
        if (!raw) return DEFAULT_SETTINGS;
        const parsed = JSON.parse(raw) as Partial<SettingsState> | null;
        if (!parsed || typeof parsed !== 'object') return DEFAULT_SETTINGS;
        return {
            density: isValidDensity(parsed.density) ? parsed.density : DEFAULT_SETTINGS.density,
            iconFirstUserIds: sanitizeIconFirstUserIds(parsed.iconFirstUserIds),
            userThemeColors: sanitizeUserThemeColors(parsed.userThemeColors),
        };
    } catch {
        return DEFAULT_SETTINGS;
    }
}

export const SettingsProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [settings, setSettings] = useState<SettingsState>(loadSettings);

    useEffect(() => {
        try {
            window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings));
        } catch {
            // localStorageが使えない環境(プライベートモード等)では単に永続化を諦める
        }
    }, [settings]);

    const value = useMemo<SettingsContextValue>(() => ({
        ...settings,
        setDensity: (density) => setSettings(s => ({ ...s, density })),
        toggleIconFirstUser: (userId) => setSettings(s => ({
            ...s,
            iconFirstUserIds: s.iconFirstUserIds.includes(userId)
                ? s.iconFirstUserIds.filter(id => id !== userId)
                : [...s.iconFirstUserIds, userId],
        })),
        setUserThemeColor: (userId, color) => setSettings(s => ({
            ...s,
            userThemeColors: { ...s.userThemeColors, [userId]: color },
        })),
    }), [settings]);

    return (
        <SettingsContext.Provider value={value}>
            {children}
        </SettingsContext.Provider>
    );
};
