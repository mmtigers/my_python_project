import React, { useCallback, useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ToastContext, ToastItem } from './toastShared';

// レベルアップ/メダル獲得などの「成功の演出」を、作業を止めるブロッキングモーダルではなく
// 自動で消える軽量トーストとして表示するための仕組み。
// 型・Context object・useToast フックは toastShared.ts / useToast.ts に分離している。

const AUTO_DISMISS_MS = 4000;

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [toasts, setToasts] = useState<ToastItem[]>([]);

    const showToast = useCallback((toast: Omit<ToastItem, 'id' | 'createdAt'>) => {
        const item: ToastItem = { ...toast, id: Date.now() + Math.random(), createdAt: Date.now() };
        setToasts(prev => [...prev, item]);

        setTimeout(() => {
            setToasts(prev => prev.filter(t => t.id !== item.id));
        }, AUTO_DISMISS_MS);
    }, []);

    const dismiss = useCallback((id: number) => {
        setToasts(prev => prev.filter(t => t.id !== id));
    }, []);

    const value = useMemo(() => ({ showToast }), [showToast]);

    return (
        <ToastContext.Provider value={value}>
            {children}

            {/* トーストスタック: 複数連続完了でも作業を止めずに積み上げて表示する */}
            <div className="fixed top-4 left-1/2 -translate-x-1/2 z-[100] flex flex-col items-center gap-2 pointer-events-none w-full max-w-sm px-4">
                <AnimatePresence>
                    {toasts.map(t => (
                        <motion.div
                            key={t.id}
                            layout
                            initial={{ opacity: 0, y: -20, scale: 0.9 }}
                            animate={{ opacity: 1, y: 0, scale: 1 }}
                            exit={{ opacity: 0, y: -10, scale: 0.95 }}
                            transition={{ type: 'spring', stiffness: 400, damping: 28 }}
                            onClick={() => dismiss(t.id)}
                            className="pointer-events-auto w-full bg-slate-800 border-2 border-yellow-500/60 rounded-lg shadow-2xl px-3 py-2 flex items-center gap-3 cursor-pointer"
                        >
                            {t.icon && <span className="text-2xl leading-none">{t.icon}</span>}
                            <div className="flex-1 min-w-0">
                                <div className="text-yellow-400 font-bold text-sm truncate">{t.title}</div>
                                {t.text && <div className="text-gray-200 text-xs truncate">{t.text}</div>}
                            </div>
                        </motion.div>
                    ))}
                </AnimatePresence>
            </div>
        </ToastContext.Provider>
    );
};
