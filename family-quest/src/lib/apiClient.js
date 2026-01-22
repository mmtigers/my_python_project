// src/lib/apiClient.js

const BASE_URL = import.meta.env.VITE_API_URL || '';

class ApiClient {
  constructor(baseUrl) {
    this.baseUrl = baseUrl;
  }

  async get(endpoint) {
    return this._request(endpoint, { method: 'GET' });
  }

  async post(endpoint, body) {
    return this._request(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });
  }

  async _request(endpoint, options) {
    const cleanEndpoint = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
    const url = `${this.baseUrl}${cleanEndpoint}`;

    try {
      const response = await fetch(url, options);
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `API Error: ${response.status}`);
      }
      return response.json();
    } catch (error) {
      console.error(`API Request Failed [${endpoint}]:`, error);
      throw error;
    }
  }

  // --- Inventory Methods ---
  // ★修正: すべてのパスの先頭に '/api/quest' を追加

  async fetchInventory(userId) {
    return this.get(`/api/quest/inventory/${userId}`);
  }

  async useItem(userId, inventoryId) {
    return this.post('/api/quest/inventory/use', { user_id: userId, inventory_id: inventoryId });
  }

  async cancelItemUsage(userId, inventoryId) {
    return this.post('/api/quest/inventory/cancel', { user_id: userId, inventory_id: inventoryId });
  }

  async consumeItem(approverId, inventoryId) {
    return this.post('/api/quest/inventory/consume', { approver_id: approverId, inventory_id: inventoryId });
  }

  // 管理者用: 承認待ちアイテム一覧を取得
  async fetchPendingInventory() {
    return this.get('/api/quest/inventory/admin/pending');
  }

  async delete(endpoint) {
    return this._request(endpoint, { method: 'DELETE' });
  }

}

export const apiClient = new ApiClient(BASE_URL);

// ▼▼▼ Guild Bounty API (修正版) ▼▼▼
// 既存の apiClient インスタンスを使うことで、BASE_URLの指定ミスを防ぎ、エラー処理を統一します

export const fetchBounties = async (userId) => {
  return apiClient.get(`api/bounties/list?user_id=${userId}`);
};

export const createBounty = async (bountyData) => {
  return apiClient.post('api/bounties/create', bountyData);
};

export const acceptBounty = async (bountyId, userId) => {
  // postメソッドの第2引数に body を渡す
  return apiClient.post(`api/bounties/${bountyId}/accept`, { user_id: userId });
};

export const completeBounty = async (bountyId, userId) => {
  // POST /api/bounties/{id}/complete
  return apiClient.post(`api/bounties/${bountyId}/complete`, { user_id: userId });
};

export const approveBounty = async (bountyId, userId) => {
  // POST /api/bounties/{id}/approve
  return apiClient.post(`api/bounties/${bountyId}/approve`, { user_id: userId });
};


export const resignBounty = async (bountyId, userId) => {
  return apiClient.post(`api/bounties/${bountyId}/resign`, { user_id: userId });
};

export const deleteBounty = async (bountyId, userId) => {
  return apiClient.delete(`api/bounties/${bountyId}?user_id=${userId}`);
};