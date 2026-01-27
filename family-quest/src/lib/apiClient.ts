// family-quest/src/lib/apiClient.ts

import { Bounty, InventoryItem, PendingInventory } from "../types";

const BASE_URL: string = import.meta.env.VITE_API_URL || '';

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

    async cancelItemUsage(userId: string, inventoryId: number): Promise<ApiResponse> {
        return this.post<ApiResponse>('/api/quest/inventory/cancel', { user_id: userId, inventory_id: inventoryId });
    }

    async consumeItem(approverId: string, inventoryId: number): Promise<ApiResponse> {
        return this.post<ApiResponse>('/api/quest/inventory/consume', { approver_id: approverId, inventory_id: inventoryId });
    }

    async fetchPendingInventory(): Promise<PendingInventory[]> {
        return this.get<PendingInventory[]>('/api/quest/inventory/admin/pending');
    }
}

export const apiClient = new ApiClient(BASE_URL);

// --- Guild Bounty API Wrappers ---

export const fetchBounties = async (userId: string): Promise<Bounty[]> => {
    return apiClient.get<Bounty[]>(`api/bounties/list?user_id=${userId}`);
};

export const createBounty = async (bountyData: Record<string, unknown>): Promise<ApiResponse> => {
    return apiClient.post<ApiResponse>('api/bounties/create', bountyData);
};

export const acceptBounty = async (bountyId: number, userId: string): Promise<ApiResponse> => {
    return apiClient.post<ApiResponse>(`api/bounties/${bountyId}/accept`, { user_id: userId });
};

export const completeBounty = async (bountyId: number, userId: string): Promise<ApiResponse> => {
    return apiClient.post<ApiResponse>(`api/bounties/${bountyId}/complete`, { user_id: userId });
};

export const approveBounty = async (bountyId: number, userId: string): Promise<ApiResponse> => {
    return apiClient.post<ApiResponse>(`api/bounties/${bountyId}/approve`, { user_id: userId });
};

export const resignBounty = async (bountyId: number, userId: string): Promise<ApiResponse> => {
    return apiClient.post<ApiResponse>(`api/bounties/${bountyId}/resign`, { user_id: userId });
};

export const deleteBounty = async (bountyId: number, userId: string): Promise<ApiResponse> => {
    return apiClient.delete<ApiResponse>(`api/bounties/${bountyId}?user_id=${userId}`);
};