-- Issue #762 (AUDIT-034) ステップ1: クエスト取消の監査証跡
--
-- `ApprovalService._revert_and_delete_history` は承認済み履歴を物理削除するため、
-- 「誰がいつ何を取り消したか」がどこにも残らず(`logger.info` はログファイルのみ)、
-- 家族の年代記(`_fetch_full_adventure_logs`)の原資である `quest_history` の行が
-- 復元不能に消える。Issue #733 の保持期間削除を有効化すると、「取消で消えた行」と
-- 「保持期間で消えた行」を区別する手段も無くなる。
--
-- 本テーブルは削除される行の内容をそのまま写し取る追記専用の監査ログである。
-- **`quest_history` の意味論は一切変えない**(論理削除にするか・取消済みを年代記に
-- 表示するかは仕様判断であり Issue #762 で未決。それを後から実データを見て
-- 決められるようにするのが本マイグレーションの目的)。
--
-- 保持期間削除(`services/db_retention_service.py`)の対象には**含めない**。
-- 取消は稀な操作で行数が増えず、`reward_history` を「残高に影響しない監査用の
-- 記録」として残す既存の判断(`services/quest/user_service.py`)と揃える。

CREATE TABLE IF NOT EXISTS quest_cancellation_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- 削除された quest_history.id。行自体は消えるため外部キーは張らない
    history_id INTEGER NOT NULL,
    -- 履歴の所有者(削除された quest_history.user_id)
    user_id TEXT,
    -- 取消を要求したユーザー。主履歴では user_id と一致するが、兄妹連携クエストの
    -- カスケード取消では相方の履歴に対して「別人が取り消した」記録になる
    cancelled_by TEXT NOT NULL,
    -- 削除された行の内容(年代記の再構成・突き合わせに必要な分)
    quest_id INTEGER,
    quest_title TEXT,
    status_before TEXT,
    completed_at DATETIME,
    exp_earned INTEGER,
    gold_earned INTEGER,
    medals_earned INTEGER,
    linked_history_id INTEGER,
    -- 1 なら連携クエストの相方としてカスケード取消された行
    cascaded INTEGER NOT NULL DEFAULT 0,
    -- 残高をロールバックしたか(status_before='approved' の場合のみ 1)
    rewards_reverted INTEGER NOT NULL DEFAULT 0,
    cancelled_at DATETIME NOT NULL
);

-- 「この履歴は取り消されたのか」を引く経路
CREATE INDEX IF NOT EXISTS idx_quest_cancellation_audit_history
    ON quest_cancellation_audit (history_id);

-- 「このユーザーの取消を新しい順に」引く経路(年代記・監査の主用途)
CREATE INDEX IF NOT EXISTS idx_quest_cancellation_audit_user_time
    ON quest_cancellation_audit (user_id, cancelled_at DESC);
