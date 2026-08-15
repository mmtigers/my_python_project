import { useContext } from 'react';
import { ToastContext, ToastContextValue } from './toastShared';

export function useToast(): ToastContextValue {
    const ctx = useContext(ToastContext);
    if (!ctx) throw new Error('useToast は ToastProvider の内側で使ってください');
    return ctx;
}
