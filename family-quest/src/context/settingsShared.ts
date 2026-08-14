import { createContext } from 'react';

// SettingsContext.tsx(Provider本体)と useSettings.ts(フック)の両方から参照する
// 型・定数・Context object をここに集約する。
// (react-refresh の "1ファイルはコンポーネントのみexportする" 制約により、
//  コンポーネントを export する SettingsContext.tsx と分離している)

export type Density = 'comfortable' | 'compact';

export const THEME_COLORS = [
    { key: 'blue', label: 'ブルー', className: 'bg-blue-500' },
    { key: 'red', label: 'レッド', className: 'bg-red-500' },
    { key: 'green', label: 'グリーン', className: 'bg-green-500' },
    { key: 'purple', label: 'パープル', className: 'bg-purple-500' },
    { key: 'pink', label: 'ピンク', className: 'bg-pink-500' },
    { key: 'orange', label: 'オレンジ', className: 'bg-orange-500' },
] as const;

export type ThemeColorKey = typeof THEME_COLORS[number]['key'];

// ユーザーのテーマカラーをパネルの枠線/リング色に変換するためのマップ。
// Tailwindはクラス名を動的生成できないため、あらかじめ全パターンを列挙しておく。
export const THEME_BORDER_CLASSES: Record<ThemeColorKey, string> = {
    blue: 'border-blue-400',
    red: 'border-red-400',
    green: 'border-green-400',
    purple: 'border-purple-400',
    pink: 'border-pink-400',
    orange: 'border-orange-400',
};

export const THEME_RING_CLASSES: Record<ThemeColorKey, string> = {
    blue: 'ring-blue-400/50',
    red: 'ring-red-400/50',
    green: 'ring-green-400/50',
    purple: 'ring-purple-400/50',
    pink: 'ring-pink-400/50',
    orange: 'ring-orange-400/50',
};

export interface SettingsState {
    density: Density;
    // 非識字年齢の子ども向け「アイコン主体」表示を適用するユーザーIDの集合。
    // 以前は FamilyDashboard.tsx に 'daughter' がハードコードされていた。
    iconFirstUserIds: string[];
    // ユーザーごとのパネル/カードのアクセントカラー
    userThemeColors: Record<string, ThemeColorKey>;
}

export const DEFAULT_SETTINGS: SettingsState = {
    density: 'comfortable',
    iconFirstUserIds: [],
    userThemeColors: {},
};

export const SETTINGS_STORAGE_KEY = 'familyQuest.settings.v1';

export interface SettingsContextValue extends SettingsState {
    setDensity: (density: Density) => void;
    toggleIconFirstUser: (userId: string) => void;
    setUserThemeColor: (userId: string, color: ThemeColorKey) => void;
}

export const SettingsContext = createContext<SettingsContextValue | null>(null);
