"""クエスト報酬の付与(services/quest/quest_service.py から分割。Issue #662)。

承認系(`ApprovalService`)と完了系(`QuestService`)の両方が使う唯一の実装。
どちらか一方のクラスに置くと他方がそのクラスを参照することになるため、
素の関数としてここに置いている。
"""
from typing import Any, Dict

import game_logic
from core import sound_manager


def apply_quest_rewards(cur, user, quest, now_iso, history_id=None, override_rewards=None) -> Dict[str, Any]:
    """クエスト報酬(gold/exp/medal)をユーザーへ加算し、履歴を確定させる。

    承認系(ApprovalService)と完了系(QuestService)の両方から呼ばれる共有処理(#662)。
    もともと QuestService のメソッドだったが self を一切使っておらず、承認系を
    別クラスへ分けると双方向の相互参照になるため、素の関数としてここへ移した。
    """
    if override_rewards:
        base_gold = override_rewards['gold']
        base_exp = override_rewards['exp']
    else:
        base_gold = quest['gold_gain']
        base_exp = quest['exp_gain']

    rewards = game_logic.GameLogic.calculate_drop_rewards(base_gold, base_exp)
    earned_gold = rewards['gold']
    earned_exp = rewards['exp']
    earned_medals = rewards['medals']
    is_lucky = rewards['is_lucky']

    new_level, new_exp_val, leveled_up = game_logic.GameLogic.calc_level_progress(
        user['level'], user['exp'], earned_exp
    )

    final_gold = user['gold'] + earned_gold

    cur.execute("""
        UPDATE quest_users
        SET level = ?, exp = ?, gold = ?, medal_count = medal_count + ?, updated_at = ?
        WHERE user_id = ?
    """, (new_level, new_exp_val, final_gold, earned_medals, now_iso, user['user_id']))

    # Q-L3(#409): メダルドロップ数も履歴(medals_earned、migration 0009)に記録し、
    # キャンセル時に _revert_and_delete_history が戻せるようにする。
    if history_id:
        # completed_at は子供が完了報告した時刻のまま維持する(承認時刻で上書きしない)。
        # 上書きしていた旧実装では、承認が翌日(weeklyなら翌週)にずれた場合に
        # process_complete_quest のスパムチェック/周期リセット判定が「本日(今週)完了済み」
        # と誤判定し、翌日分の完了報告ができなくなる不具合があった(#93)。
        cur.execute("UPDATE quest_history SET status='approved', gold_earned=?, exp_earned=?, medals_earned=? WHERE id=?",
                   (earned_gold, earned_exp, earned_medals, history_id))
    else:
        cur.execute("""
            INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, medals_earned, completed_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'approved')
        """, (user['user_id'], quest['quest_id'], quest['title'], earned_exp, earned_gold, earned_medals, now_iso))

    if leveled_up:
        sound_manager.play("level_up")
    elif is_lucky:
        sound_manager.play("medal_get")
    elif not history_id:
        sound_manager.play("quest_clear")

    return {
        "status": "success",
        "leveledUp": leveled_up, "newLevel": new_level,
        "earnedGold": earned_gold, "earnedExp": earned_exp, "earnedMedals": earned_medals
    }
