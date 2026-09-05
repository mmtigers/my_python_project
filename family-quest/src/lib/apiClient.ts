// family-quest/src/lib/apiClient.ts

import { InventoryResponse } from "../types";

// 現在の環境に最も適したBASE_URLを動的に判定する
const getBaseUrl = (): string => {
    // 1. .envで明示的に指定されている場合 (ローカル開発環境など)
    if (import.meta.env.VITE_API_URL) {
        return import.meta.env.VITE_API_URL;
    }
    // 2. 指定がない場合は、ブラウザが現在アクセスしているドメインをそのまま使用する
    return typeof window !== 'undefined' ? window.location.origin : '';
};

const BASE_URL: string = getBaseUrl();

interface RequestOptions extends RequestInit {
    headers?: Record<string, string>;
}

// 汎用的なAPIレスポンス型
export interface ApiResponse<T = unknown> {
    status?: string;
    data?: T;
    [key: string]: unknown;
}

interface ErrorResponse {
    detail?: string | unknown;
}

// #412(F-L3): FastAPIのバリデーションエラー(422)は detail が
// [{loc, msg, type}, ...] という配列で返る。以前は文字列以外の detail を
// 一律 `API Error: ${status}` にフォールバックしていたため、422の具体的な
// エラー内容(どのフィールドが不正か等)がユーザーにもconsoleにも伝わらなかった。
function extractDetailMessage(detail: unknown): string | undefined {
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
        const messages = detail
            .map(item => (item && typeof item === 'object' && 'msg' in item)
                ? String((item as { msg: unknown }).msg)
                : null)
            .filter((m): m is string => !!m);
        if (messages.length > 0) return messages.join(' / ');
    }
    return undefined;
}

class ApiClient {
    private baseUrl: string;

    constructor(baseUrl: string) {
        this.baseUrl = baseUrl;
    }

    async get<T>(endpoint: string): Promise<T> {
        return this._request<T>(endpoint, { method: 'GET' });
    }

    async post<T>(endpoint: string, body: Record<string, unknown>): Promise<T> {
        return this._request<T>(endpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(body),
        });
    }

    // multipart/form-data 用のPOST。Content-Typeを明示的に指定しないことで、
    // ブラウザにboundary付きのヘッダーを自動生成させる（手動指定するとboundaryが
    // 欠落しリクエストが壊れるため）。
    async postForm<T>(endpoint: string, formData: FormData): Promise<T> {
        return this._request<T>(endpoint, {
            method: 'POST',
            body: formData,
        });
    }

    async put<T>(endpoint: string, body: Record<string, unknown>): Promise<T> {
        return this._request<T>(endpoint, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(body),
        });
    }

    async delete<T>(endpoint: string): Promise<T> {
        return this._request<T>(endpoint, { method: 'DELETE' });
    }

    private async _request<T>(endpoint: string, options: RequestOptions): Promise<T> {
        const cleanEndpoint = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
        const url = `${this.baseUrl}${cleanEndpoint}`;

        try {
            const response = await fetch(url, options);
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({})) as ErrorResponse;
                const errorMessage = extractDetailMessage(errorData.detail) ?? `API Error: ${response.status}`;
                throw new Error(errorMessage);
            }
            // #412(F-L3): 204 No Content や空ボディの成功レスポンス(cancel/reject等の
            // 一部エンドポイント)に対して response.json() を呼ぶと
            // "Unexpected end of JSON input" で失敗していた。ボディを一度テキストとして
            // 読み、空ならパースせずに undefined を返す。
            if (response.status === 204) return undefined as T;
            const text = await response.text();
            return (text ? JSON.parse(text) : undefined) as T;
        } catch (error) {
            console.error(`API Request Failed [${endpoint}]:`, error);
            // #412(F-L3): 上のthrow new Error(errorMessage)(HTTPエラー応答)はそのまま
            // 透過させる。それ以外(fetch自体の失敗によるTypeError、あるいは不正な
            // JSONによるSyntaxError)は、"Failed to fetch"のような生のブラウザ文言が
            // そのままモーダルに表示されてしまうのを避け、意味の伝わる文言に変換する。
            if (error instanceof TypeError || error instanceof SyntaxError) {
                throw new Error('通信エラーが発生しました。ネットワーク接続をご確認のうえ、再度お試しください。');
            }
            throw error;
        }
    }

    // --- Inventory Methods ---
    async fetchInventory(userId: string): Promise<InventoryResponse> {
        return this.get<InventoryResponse>(`/api/quest/inventory/${userId}`);
    }

    async useItem(userId: string, inventoryId: number): Promise<ApiResponse> {
        return this.post<ApiResponse>('/api/quest/inventory/use', { user_id: userId, inventory_id: inventoryId });
    }
}

export const apiClient = new ApiClient(BASE_URL);