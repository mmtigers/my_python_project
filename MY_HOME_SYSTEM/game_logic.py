import math
import random
from typing import Tuple, Dict, Any, Optional

# レベルアップ計算などのコアロジック
class GameLogic:
    """
    ゲームルールの計算ロジックを担当するクラス
    DB接続は行わず、純粋な入出力のみを扱う
    """

    @staticmethod
    def calculate_next_level_exp(level: int) -> int:
        """次のレベルに必要な経験値を計算"""
        return math.floor(100 * math.pow(1.2, level - 1))

    @staticmethod
    def calculate_max_hp(level: int) -> int:
        """レベルに応じた最大HPを計算"""
        return level * 20 + 5

    @classmethod
    def calc_level_progress(cls, current_level: int, current_exp: int, added_exp: int) -> Tuple[int, int, bool]:
        """
        経験値を加算し、レベルアップ判定を行う
        Returns: (new_level, new_exp, is_leveled_up)
        """
        total_exp = current_exp + added_exp
        new_level = current_level
        leveled_up = False
        
        req_exp = cls.calculate_next_level_exp(new_level)
        
        while total_exp >= req_exp:
            total_exp -= req_exp
            new_level += 1
            leveled_up = True
            req_exp = cls.calculate_next_level_exp(new_level)
            
        return new_level, total_exp, leveled_up

    @classmethod
    def calc_level_down(cls, current_level: int, current_exp: int, removed_exp: int) -> Tuple[int, int]:
        """
        経験値を減算し、レベルダウン判定を行う（キャンセル時など）
        Returns: (new_level, new_exp)
        """
        raw_exp_diff = current_exp - removed_exp
        new_level = current_level
        new_exp = raw_exp_diff
        
        # レベルダウン処理 (Expがマイナスになった場合)
        while new_exp < 0 and new_level > 1:
            new_level -= 1
            prev_level_max = cls.calculate_next_level_exp(new_level)
            new_exp += prev_level_max
            
        if new_exp < 0: 
            new_exp = 0
            
        return new_level, new_exp

    @staticmethod
    def calculate_drop_rewards(base_gold: int, base_exp: int) -> Dict[str, Any]:
        """
        クエスト報酬の計算（ランダムドロップなど）
        Phase 2でここに「天気ボーナス」「時間補正」などを追加可能にする
        """
        # メダルドロップ判定 (5%)
        # 将来的には引数で確率を変えられるようにする
        medal_chance = 0.05
        earned_medals = 1 if random.random() < medal_chance else 0
        
        return {
            "gold": base_gold,
            "exp": base_exp,
            "medals": earned_medals,
            "is_lucky": (earned_medals > 0)
        }