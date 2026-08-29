// family-quest/src/lib/apiClient.ts

import { InventoryItem } from "../types";

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
                const errorMessage = typeof errorData.detail === 'string'
                    ? errorData.detail
                    : `API Error: ${response.status}`;
                throw new Error(errorMessage);
            }
            return await response.json() as T;
        } catch (error) {
            console.error(`API Request Failed [${endpoint}]:`, error);
            throw error;
        }
    }

    // --- Inventory Methods ---
    // 配列を直接返すように型指定 (APIがリストを返す前提)
    async fetchInventory(userId: string): Promise<InventoryItem[]> {
        return this.get<InventoryItem[]>(`/api/quest/inventory/${userId}`);
    }

    async useItem(userId: string, inventoryId: number): Promise<ApiResponse> {
        return this.post<ApiResponse>('/api/quest/inventory/use', { user_id: userId, inventory_id: inventoryId });
    }
}

export const apiClient = new ApiClient(BASE_URL);