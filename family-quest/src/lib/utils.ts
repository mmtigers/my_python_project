import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Tailwindのクラスをマージするユーティリティ
 * 例: cn("bg-red-500", isTrue && "p-4", "p-2") -> "bg-red-500 p-4" (p-2は消える)
 */
export function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs));
}