import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Tailwindのクラスをマージするユーティリティ
 * 例: cn("bg-red-500", isTrue && "p-4", "p-2") -> "bg-red-500 p-4" (p-2は消える)
 */
export function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs));
}

/**
 * M-9-5バグ修正: アバターURLが自サーバーの相対パス(/uploads/...)であることを
 * 確認するためのチェック。単純な startsWith('/') だと、プロトコル相対URL
 * ("//evil.example/x")もマッチしてしまう(ブラウザは "//host/path" を
 * 現在のプロトコルでの外部ホストへのリンクとして解決するため、外部画像への
 * 差し替えを許してしまう)。"//" で始まるものは除外する。
 */
export function isSameOriginAvatarPath(url: string | undefined | null): url is string {
    return !!url && url.startsWith('/') && !url.startsWith('//');
}