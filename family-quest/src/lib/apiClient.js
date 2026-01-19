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

}

export const apiClient = new ApiClient(BASE_URL);