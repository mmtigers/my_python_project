import React, { useEffect } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "./Button";

interface ModalProps {
    isOpen: boolean;
    onClose: () => void;
    title?: React.ReactNode;
    children: React.ReactNode;
    footer?: React.ReactNode;
    maxWidth?: "sm" | "md" | "lg" | "xl";
}

export const Modal: React.FC<ModalProps> = ({
    isOpen,
    onClose,
    title,
    children,
    footer,
    maxWidth = "sm"
}) => {
    // ESCキーで閉じる
    useEffect(() => {
        const handleEsc = (e: KeyboardEvent) => {
            if (e.key === "Escape") onClose();
        };
        if (isOpen) window.addEventListener("keydown", handleEsc);
        return () => window.removeEventListener("keydown", handleEsc);
    }, [isOpen, onClose]);

    if (!isOpen) return null;

    const maxWidthClasses = {
        sm: "max-w-sm",
        md: "max-w-md",
        lg: "max-w-lg",
        xl: "max-w-xl",
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
            {/* 背景 (Backdrop) */}
            <div
                className="absolute inset-0 bg-black/80 backdrop-blur-sm"
                onClick={onClose}
            />

            {/* コンテンツ本体 */}
            <div className={cn(
                "relative bg-slate-800 border-2 border-slate-600 rounded-lg shadow-2xl w-full overflow-hidden animate-in zoom-in-95 duration-200",
                maxWidthClasses[maxWidth]
            )}>
                {/* ヘッダー */}
                {(title || onClose) && (
                    <div className="flex items-center justify-between p-4 border-b border-slate-700 bg-slate-900/50">
                        <h3 className="text-lg font-bold text-white flex-1">{title}</h3>
                        <Button variant="ghost" size="icon" onClick={onClose} className="h-8 w-8 -mr-2">
                            <X size={18} />
                        </Button>
                    </div>
                )}

                {/* ボディ */}
                <div className="p-6 text-slate-200">
                    {children}
                </div>

                {/* フッター */}
                {footer && (
                    <div className="p-4 bg-slate-900/50 border-t border-slate-700 flex justify-end gap-3">
                        {footer}
                    </div>
                )}
            </div>
        </div>
    );
};