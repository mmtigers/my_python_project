import { useContext } from 'react';
import { SettingsContext, SettingsContextValue } from './settingsShared';

export function useSettings(): SettingsContextValue {
    const ctx = useContext(SettingsContext);
    if (!ctx) throw new Error('useSettings は SettingsProvider の内側で使ってください');
    return ctx;
}
