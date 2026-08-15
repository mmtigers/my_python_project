import React, { useEffect, useMemo, useState } from 'react';
import {
    SettingsContext, SettingsState, SettingsContextValue,
    DEFAULT_SETTINGS, SETTINGS_STORAGE_KEY,
} from './settingsShared';

// アプリ全体の表示設定。localStorage に永続化し、次回起動時も引き継ぐ。
// バックエンドに保存する必要のない「この端末でのUI好み」のみを扱う。
// 型・定数・useSettings フックは settingsShared.ts / useSettings.ts に分離している
// (react-refresh の「1ファイルはコンポーネントのみexportする」制約のため)。

function loadSettings(): SettingsState {
    if (typeof window === 'undefined') return DEFAULT_SETTINGS;
    try {
        const raw = window.localStorage.getItem(SETTINGS_STORAGE_KEY);
        if (!raw) return DEFAULT_SETTINGS;
        const parsed = JSON.parse(raw);
        return {
            ...DEFAULT_SETTINGS,
            ...parsed,
            userThemeColors: { ...DEFAULT_SETTINGS.userThemeColors, ...(parsed.userThemeColors || {}) },
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
