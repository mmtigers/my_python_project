import { createContext } from 'react';

// ToastContext.tsx(Provider本体)と useToast.ts(フック)の両方から参照する
// 型・Context object をここに集約する。
// (react-refresh の「1ファイルはコンポーネントのみexportする」制約により分離している)

export interface ToastItem {
    id: number;
    title: string;
    text?: string;
    icon?: string;
    createdAt: number;
}

export interface ToastContextValue {
    showToast: (toast: Omit<ToastItem, 'id' | 'createdAt'>) => void;
    history: ToastItem[];
    clearHistory: () => void;
}

export const ToastContext = createContext<ToastContextValue | null>(null);
