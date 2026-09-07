"""services/quest_service.py から分割(Issue #550)。"""
import os
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

import common
import config
from services.quest.locks import logger


class UserService:
    def get_family_chronicle(self) -> Dict[str, Any]:
        with common.get_db_cursor() as cur:
            users = cur.execute("SELECT level, gold FROM quest_users").fetchall()
            total_level = sum(u['level'] for u in users) if users else 0
            total_gold = sum(u['gold'] for u in users) if users else 0
            # process_reject_quest が却下履歴を残す(status='rejected')ようになったため、
            # 却下された申請を「達成したクエスト数」に含めないよう明示的に除外する。
            # Q-L5(#409): 承認待ち(pending)行と、use_item が quest_id=0 で挿入する
            # 「アイテム使用」行は達成クエスト数に含めない。
            res = cur.execute("SELECT COUNT(*) as count FROM quest_history WHERE status = 'approved' AND quest_id != 0").fetchone()
            total_quests = res['count'] if res else 0

            if total_level < 10:
                rank = "駆け出しの家族"
            elif total_level < 30:
                rank = "新進気鋭のパーティ"
            elif total_level < 60:
                rank = "熟練のクラン"
            else:
                rank = "伝説のギルド"

            logs = self._fetch_full_adventure_logs(cur)

        return {
            "stats": {"totalLevel": total_level, "totalGold": total_gold, "totalQuests": total_quests, "partyRank": rank},
            "chronicle": logs
        }

    def _fetch_full_adventure_logs(self, cur) -> List[dict]:
        # Q-L5: use_item が quest_id=0 で挿入する「アイテム使用」行はクエスト達成ではないため除外する
        q_rows = cur.execute("SELECT 'quest' as type, user_id, quest_title as title, gold_earned as gold, exp_earned as exp, completed_at as ts FROM quest_history WHERE status='approved' AND quest_id != 0 ORDER BY completed_at DESC LIMIT 100").fetchall()
        r_rows = cur.execute("SELECT 'reward' as type, user_id, reward_title as title, cost_gold as gold, 0 as exp, redeemed_at as ts FROM reward_history ORDER BY redeemed_at DESC LIMIT 100").fetchall()

        all_events = sorted(q_rows + r_rows, key=lambda x: x['ts'], reverse=True)[:100]
        user_info = {row['user_id']: {"name": row['name'], "avatar": row['avatar']} for row in cur.execute("SELECT user_id, name, avatar FROM quest_users")}

        formatted = []
        for ev in all_events:
            u = user_info.get(ev['user_id'], {"name": "旅人", "avatar": "👤"})
            text = ""
            if ev['type'] == 'quest':
                text = f"{u['name']}は {ev['title']} を達成した！"
            elif ev['type'] == 'reward':
                text = f"{u['name']}は {ev['title']} を獲得した！"

            formatted.append({
                "type": ev['type'], "userId": ev['user_id'], "userName": u['name'], "userAvatar": u['avatar'],
                "title": ev['title'], "text": text, "gold": ev['gold'], "exp": ev['exp'],
                "timestamp": ev['ts'],
                "dateStr": ev['ts'].split('T')[0] if 'T' in ev['ts'] else ev['ts'].split(' ')[0]
            })
        return formatted

    def update_avatar(self, user_id: str, avatar_url: str) -> Dict[str, Any]:
        with common.get_db_cursor(commit=True) as cur:
            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (user_id,)).fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            old_avatar = user['avatar']

            cur.execute("UPDATE quest_users SET avatar = ?, updated_at = ? WHERE user_id = ?",
                       (avatar_url, common.get_now_iso(), user_id))

            # #372: 旧アバターのファイルを他のユーザーも参照している場合(同じ /uploads/ パスを
            # 指定された場合)、物理削除するとそのユーザーのアバターが404になる。
            # 他ユーザーからの参照が残っている限りファイルは削除しない。
            still_referenced = cur.execute(
                "SELECT 1 FROM quest_users WHERE avatar = ? AND user_id != ? LIMIT 1",
                (old_avatar, user_id),
            ).fetchone() is not None

            logger.info(f"Avatar Updated: User={user_id}, URL={avatar_url}")

        if not still_referenced:
            self._delete_orphaned_avatar(old_avatar, avatar_url)
        return {"status": "updated", "avatar": avatar_url}

    def _delete_orphaned_avatar(self, old_avatar: Optional[str], new_avatar: str) -> None:
        """アップロード済みの旧アバターファイルが差し替え後にディスクへ残り続けるのを防ぐ。
        絵文字などアップロードファイル以外の値や、他ユーザーと共有され得ない
        /uploads/ 配下のファイルのみを対象とし、パストラバーサルを避けるため
        ファイル名部分のみをUPLOAD_DIR基準で解決する。"""
        if not old_avatar or old_avatar == new_avatar:
            return
        if not old_avatar.startswith("/uploads/"):
            return

        filename = os.path.basename(old_avatar)
        file_path = os.path.join(config.UPLOAD_DIR, filename)
        if os.path.dirname(file_path) != os.path.normpath(config.UPLOAD_DIR):
            return

        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Orphaned avatar removed: {file_path}")
        except OSError as e:
            logger.warning(f"Failed to remove orphaned avatar {file_path}: {e}")

    def delete_unlinked_avatar(self, filename: str) -> bool:
        """#442: AvatarUploader.tsxの2段階アップロード(画像アップロード→ユーザーへの
        紐付け)のうち2段階目(/user/update)が失敗した際のロールバック用。まだどの
        ユーザーにも紐付けられていない画像を削除し、孤立ファイルとしてディスクに
        残り続けるのを防ぐ。

        _delete_orphaned_avatarと同様、ファイル名部分のみをUPLOAD_DIR基準で解決し
        パストラバーサルを防ぐ。加えて、削除しようとしているファイルを既に
        どこかのユーザーが参照している場合は削除しない(競合するアップロード等で
        誤って現役のアバターを消さないための安全策)。

        Returns:
            bool: 実際に削除した場合True。参照中・パス不正・ファイル未存在等で
                削除しなかった場合False。
        """
        filename = os.path.basename(filename)
        file_path = os.path.join(config.UPLOAD_DIR, filename)
        if os.path.dirname(file_path) != os.path.normpath(config.UPLOAD_DIR):
            return False

        avatar_value = f"/uploads/{filename}"
        with common.get_db_cursor() as cur:
            still_referenced = cur.execute(
                "SELECT 1 FROM quest_users WHERE avatar = ? LIMIT 1", (avatar_value,)
            ).fetchone() is not None
        if still_referenced:
            return False

        try:
            if not os.path.exists(file_path):
                return False
            os.remove(file_path)
            logger.info(f"Unlinked avatar removed (rollback): {file_path}")
            return True
        except OSError as e:
            logger.warning(f"Failed to remove unlinked avatar {file_path}: {e}")
            return False
