"""services/quest_service.py から分割(Issue #550)。"""
import os
import uuid
from typing import Any, Dict, List, Optional

import aiofiles
from fastapi import HTTPException, UploadFile

import common
import config
from services.quest.locks import ROLE_ADULT, _get_user_balance_lock, logger

# Issue #551: routers/quest_router.py の upload_image に直書きされていた
# 画像保存ロジック(拡張子/マジックバイト検証・サイズ上限付き保存)をこちらへ移した。
# 検証失敗はHTTPExceptionではなくValueError派生のドメイン例外で表現し、
# ルーター側でHTTPステータスコードへマッピングする(CLAUDE.mdの「ルーターは
# パース・検証のみ、ロジックはservices/*.pyへ委譲する」規約に合わせるため)。
_ALLOWED_AVATAR_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


class InvalidImageError(ValueError):
    """アップロードされたファイルが画像として不正(拡張子不許可・マジックバイト不一致等)。
    ルーターはこれを400にマッピングする。"""


class ImageTooLargeError(ValueError):
    """アップロードされたファイルがconfig.UPLOAD_MAX_FILE_SIZE_MBを超過している。
    ルーターはこれを413にマッピングする。"""


def _validate_image_header(header: bytes) -> bool:
    if header.startswith(b'\xff\xd8\xff'):
        return True
    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        return True
    if header.startswith(b'GIF87a') or header.startswith(b'GIF89a'):
        return True
    if header.startswith(b'RIFF') and header[8:12] == b'WEBP':
        return True
    return False


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

    def reset_user_data(self, admin_id: str, target_user_id: str) -> Dict[str, Any]:
        """Issue #547: reset_game.py の対話的リセットをAPI化したもの。

        reset_game.py は unified_server とは別プロセス(別のPythonインタプリタ)で
        動くため、たとえ同じモジュールをimportしても _get_user_balance_lock が
        保持するロックオブジェクト(プロセス内メモリ上のthreading.Lock)を稼働中の
        サーバープロセスと共有できない。#544で追加したBEGIN IMMEDIATEはDB側の
        原子性のみを保証するため、process_approve_quest等の「SELECT→Pythonで
        計算→絶対値SET」というread-modify-writeとreset_game.pyの直接DB操作が
        交錯すると、承認側のUPDATEがリセット結果を上書きしうる問題が残っていた。
        このメソッドをサービス層に置き、quest_users/quest_history/user_inventoryを
        書き換える他の全経路(完了・承認・取消・購入)と同じ_get_user_balance_lockの
        中で実行することで、reset_game.py側もこのAPIを呼ぶ限り直列化される。
        """
        with _get_user_balance_lock(target_user_id):
            return self._reset_user_data_locked(admin_id, target_user_id)

    def _reset_user_data_locked(self, admin_id: str, target_user_id: str) -> Dict[str, Any]:
        with common.get_db_cursor(commit=True) as cur:
            admin = cur.execute("SELECT role FROM quest_users WHERE user_id = ?", (admin_id,)).fetchone()
            if not admin or admin['role'] != ROLE_ADULT:
                raise HTTPException(status_code=403, detail="リセット権限がありません")

            target = cur.execute("SELECT user_id FROM quest_users WHERE user_id = ?", (target_user_id,)).fetchone()
            if not target:
                raise HTTPException(status_code=404, detail="対象ユーザーが見つかりません")

            # #544由来: quest_usersのゼロ化に加え、quest_history/user_inventoryも
            # 同一トランザクションで削除する(reward_history(購入ログ)は残高に
            # 影響しない監査用の記録のため対象外)。
            cur.execute(
                "UPDATE quest_users SET level = 1, exp = 0, gold = 0, medal_count = 0 WHERE user_id = ?",
                (target_user_id,),
            )
            cur.execute("DELETE FROM quest_history WHERE user_id = ?", (target_user_id,))
            deleted_history = cur.rowcount
            cur.execute("DELETE FROM user_inventory WHERE user_id = ?", (target_user_id,))
            deleted_inventory = cur.rowcount

            logger.info(
                f"User Data Reset: Admin={admin_id}, Target={target_user_id}, "
                f"deleted_history={deleted_history}, deleted_inventory={deleted_inventory}"
            )

        return {
            "status": "reset",
            "deletedHistoryCount": deleted_history,
            "deletedInventoryCount": deleted_inventory,
        }

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

    async def save_avatar_image(self, file: UploadFile) -> str:
        """
        アップロードされたファイルを拡張子・マジックバイトで画像として検証し、
        UUID採番したファイル名で config.UPLOAD_DIR 配下へ保存する。戻り値は
        保存先を指す相対URL(例: "/uploads/xxxx.png")。

        検証失敗(ファイル名なし・拡張子不許可・マジックバイト不一致)は
        InvalidImageError、サイズ上限超過は ImageTooLargeError を送出する。
        いずれもルーター(quest_router.upload_image)がHTTPExceptionへ変換する。
        """
        # Q-L6(#409): filename が無い multipart は以前 os.path.splitext(None) の TypeError → 500 だった
        if not file.filename:
            raise InvalidImageError("ファイル名がありません")
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext not in _ALLOWED_AVATAR_EXTENSIONS:
            raise InvalidImageError("許可されていないファイル形式です(拡張子)")

        header = await file.read(12)
        if not _validate_image_header(header):
            logger.warning(f"Invalid file header detected. Ext: {file_ext}")
            raise InvalidImageError("ファイルの内容が画像として認識できません")

        await file.seek(0)
        new_filename = f"{uuid.uuid4()}{file_ext}"
        file_path = os.path.join(config.UPLOAD_DIR, new_filename)

        try:
            # M-9-3: ファイルサイズ上限を設けず、チャンクを読めるだけ書き込み続けると
            # 巨大アップロードでディスクを圧迫し得た。書き込みながら累計サイズを
            # 追跡し、上限超過時は書きかけのファイルを削除して413を返す。
            max_bytes = config.UPLOAD_MAX_FILE_SIZE_MB * 1024 * 1024
            total_bytes = 0
            too_large = False
            async with aiofiles.open(file_path, "wb") as buffer:
                while content := await file.read(1024 * 1024):
                    total_bytes += len(content)
                    if total_bytes > max_bytes:
                        too_large = True
                        break
                    await buffer.write(content)

            if too_large:
                if os.path.exists(file_path):
                    os.remove(file_path)
                raise ImageTooLargeError(
                    f"ファイルサイズが上限({config.UPLOAD_MAX_FILE_SIZE_MB}MB)を超えています"
                )
        except ImageTooLargeError:
            raise
        except Exception:
            # Q-L6(#409): 書き込み途中(ディスクフル等)の例外では書きかけファイルが残っていた
            logger.exception(f"Avatar image write failed: {file_path}")
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
            raise

        logger.info(f"Image Uploaded: {new_filename}")
        return f"/uploads/{new_filename}"

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
