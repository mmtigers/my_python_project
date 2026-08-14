-- 兄妹連携クエスト用に、quest_history 同士を相互に連結するカラムを追加する。
-- target_user='siblings' のクエストを完了報告すると、対象の子ども2人分の
-- quest_history 行(両方 status='pending')が作成され、互いの linked_history_id で
-- 連結される。承認・却下・取り消しは連結された2行に対してアトミックにカスケードする。
ALTER TABLE quest_history ADD COLUMN linked_history_id INTEGER DEFAULT NULL;
