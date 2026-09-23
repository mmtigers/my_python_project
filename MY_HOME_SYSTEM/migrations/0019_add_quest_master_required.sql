-- クエストを「毎日の必須クエスト」と「ボーナスクエスト」に分けて表示するため、
-- quest_master に required カラムを追加する(要件確認済み、2026-09-23)。
-- type(daily/infinite/special/limited/random)は「出現頻度」の意味であり、
-- 「重要度(常時表示するかどうか)」とは独立に管理したいため、既存の type を
-- 転用せず新規カラムとした。既定値は 1(必須)とし、既存クエストのうち
-- ボーナス扱いにするものは quest_data.py 側で明示的に 0 を指定する
-- (sync_master_data が次回同期時に上書きする)。
ALTER TABLE quest_master ADD COLUMN required INTEGER NOT NULL DEFAULT 1;
