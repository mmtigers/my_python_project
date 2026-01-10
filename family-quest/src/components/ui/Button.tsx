import React from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

type ButtonVariant = "primary" | "secondary" | "danger" | "success" | "ghost" | "outline";
type ButtonSize = "sm" | "md" | "lg" | "icon";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
    variant?: ButtonVariant;
    size?: ButtonSize;
    isLoading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
    ({ className, variant = "primary", size = "md", isLoading, children, disabled, ...props }, ref) => {

        // ベーススタイル（共通）
        const baseStyles = "inline-flex items-center justify-center rounded font-bold transition-all focus:outline-none focus:ring-2 focus:ring-offset-2 disabled:opacity-50 disabled:pointer-events-none active:scale-95";

        // バリエーション（色・見た目）
        const variants = {
            primary: "bg-blue-600 text-white border-2 border-blue-400 hover:bg-blue-500 shadow-lg shadow-blue-900/50",
            secondary: "bg-slate-700 text-slate-200 border-2 border-slate-500 hover:bg-slate-600",
            success: "bg-green-600 text-white border-2 border-green-400 hover:bg-green-500 shadow-lg shadow-green-900/50",
            danger: "bg-red-600 text-white border-2 border-red-400 hover:bg-red-500 shadow-lg shadow-red-900/50",
            ghost: "bg-transparent text-slate-300 hover:text-white hover:bg-slate-800",
            outline: "bg-transparent border-2 border-slate-600 text-slate-300 hover:border-slate-400 hover:text-white",
        };

        // サイズ
        const sizes = {
            sm: "h-8 px-3 text-xs",
            md: "h-10 px-4 py-2 text-sm",
            lg: "h-12 px-6 text-base",
            icon: "h-10 w-10",
        };

        return (
            <button
                ref={ref}
                className={cn(baseStyles, variants[variant], sizes[size], className)}
                disabled={disabled || isLoading}
                {...props}
            >
                {isLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {children}
            </button>
        );
    }
);

Button.displayName = "Button";