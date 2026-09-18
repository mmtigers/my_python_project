// #393: 選択中ユーザーをlocalStorageに永続化する。インデックスではなくuser_idを保存する
// ことで、メンバーの並び順が変わったりメンバーが減っても、対応する人を正しく再選択でき
// (見つからなければ0番目にフォールバックする)、以前のように保存していたindexが範囲外に
// なって「接続エラー(guest)」カードが出る事故が起きない。
const CURRENT_USER_STORAGE_KEY = 'familyQuest.currentUserId.v1';

export function loadSavedUserId(): string | null {
    if (typeof window === 'undefined') return null;
    try {
        const raw = window.localStorage.getItem(CURRENT_USER_STORAGE_KEY);
        // 形状検証: 空文字列や(将来の形式変更等による)非文字列相当の値は無視する
        return typeof raw === 'string' && raw.length > 0 ? raw : null;
    } catch {
        return null;
    }
}

export function saveCurrentUserId(userId: string): void {
    try {
        window.localStorage.setItem(CURRENT_USER_STORAGE_KEY, userId);
    } catch {
        // localStorageが使えない環境(プライベートモード等)では永続化を諦める
    }
}
