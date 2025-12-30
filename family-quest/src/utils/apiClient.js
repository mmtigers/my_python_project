/**
 * API通信を管理するクライアントクラス
 * 環境変数 VITE_API_URL があればそれをベースURLとして使用します。
 */

// 開発環境と本番環境で接続先を切り替えるためのベースURL設定
// .env ファイル等で VITE_API_URL を指定可能にします
const BASE_URL = import.meta.env.VITE_API_URL || '';

class ApiClient {
  constructor(baseUrl) {
    this.baseUrl = baseUrl;
  }

  /**
   * GETリクエストを実行
   * @param {string} endpoint - APIのエンドポイント (例: '/api/quest/data')
   */
  async get(endpoint) {
    return this._request(endpoint, { method: 'GET' });
  }

  /**
   * POSTリクエストを実行
   * @param {string} endpoint 
   * @param {object} body - JSONボディ
   */
  async post(endpoint, body) {
    return this._request(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });
  }

  /**
   * 共通リクエスト処理
   * エラーレスポンスのハンドリングなどを統一
   */
  async _request(endpoint, options) {
    // URL結合時のスラッシュ重複防止
    const cleanEndpoint = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
    const url = `${this.baseUrl}${cleanEndpoint}`;

    try {
      const response = await fetch(url, options);
      
      // レスポンスがJSONでない場合も考慮しつつパース
      const data = await response.json().catch(() => null);

      if (!response.ok) {
        // バックエンドからのエラーメッセージがあればそれを使用
        const errorMessage = data?.detail || data?.message || `Error ${response.status}: ${response.statusText}`;
        throw new Error(errorMessage);
      }
      
      return data;
    } catch (error) {
      console.error(`API Request Failed [${endpoint}]:`, error);
      throw error; // 呼び出し元でUIへの通知などを行うために再スロー
    }
  }
}

// シングルトンインスタンスとしてエクスポート
export const apiClient = new ApiClient(BASE_URL);