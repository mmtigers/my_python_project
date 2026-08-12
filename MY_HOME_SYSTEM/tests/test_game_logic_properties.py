# MY_HOME_SYSTEM/tests/test_game_logic_properties.py
"""
game_logic.py (DB接続を持たない純粋関数)のproperty-based test。

レベル・経験値の計算はゲーム内経済の根幹であり、想定外の入力
(巨大な経験値、負の値、極端なレベル)でも例外を投げたり負の状態に
陥ったりしないことをhypothesisで広く検証する。
"""
import os
import sys

from hypothesis import given, settings, strategies as st

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from game_logic import GameLogic


class TestCalculateNextLevelExp:
    @given(level=st.integers(min_value=1, max_value=200))
    def test_always_positive(self, level):
        assert GameLogic.calculate_next_level_exp(level) > 0

    @given(level=st.integers(min_value=1, max_value=200))
    def test_monotonically_non_decreasing_with_level(self, level):
        assert GameLogic.calculate_next_level_exp(level + 1) >= GameLogic.calculate_next_level_exp(level)

    def test_known_values(self):
        assert GameLogic.calculate_next_level_exp(1) == 100
        assert GameLogic.calculate_next_level_exp(2) == 120


class TestCalcLevelProgress:
    @given(
        current_level=st.integers(min_value=1, max_value=100),
        current_exp=st.integers(min_value=0, max_value=10_000),
        added_exp=st.integers(min_value=0, max_value=1_000_000),
    )
    @settings(max_examples=200)
    def test_new_level_never_decreases_and_exp_never_negative(self, current_level, current_exp, added_exp):
        new_level, new_exp, _leveled_up = GameLogic.calc_level_progress(current_level, current_exp, added_exp)
        assert new_level >= current_level
        assert new_exp >= 0

    @given(data=st.data())
    def test_zero_added_exp_never_levels_up(self, data):
        """
        current_exp が「そのレベルで到達可能な範囲(0 <= exp < 次レベル必要経験値)」に
        収まっている限り、経験値0の加算ではレベルアップしないこと。
        (calc_level_progress自体がこの不変条件を維持する呼び出し元前提で作られており、
        現状exp >= 必要経験値という状態は通常発生しない)
        """
        current_level = data.draw(st.integers(min_value=1, max_value=50))
        max_valid_exp = GameLogic.calculate_next_level_exp(current_level) - 1
        current_exp = data.draw(st.integers(min_value=0, max_value=max_valid_exp))

        new_level, new_exp, leveled_up = GameLogic.calc_level_progress(current_level, current_exp, 0)
        assert leveled_up is False
        assert new_level == current_level
        assert new_exp == current_exp

    def test_exact_boundary_triggers_level_up(self):
        """必要経験値ちょうどでレベルアップすること(境界値)"""
        required = GameLogic.calculate_next_level_exp(1)
        new_level, new_exp, leveled_up = GameLogic.calc_level_progress(1, 0, required)
        assert leveled_up is True
        assert new_level == 2
        assert new_exp == 0

    def test_one_exp_under_boundary_does_not_level_up(self):
        required = GameLogic.calculate_next_level_exp(1)
        new_level, new_exp, leveled_up = GameLogic.calc_level_progress(1, 0, required - 1)
        assert leveled_up is False
        assert new_level == 1
        assert new_exp == required - 1


class TestCalcLevelDown:
    @given(
        current_level=st.integers(min_value=1, max_value=100),
        current_exp=st.integers(min_value=0, max_value=10_000),
        removed_exp=st.integers(min_value=0, max_value=1_000_000),
    )
    @settings(max_examples=200)
    def test_level_never_drops_below_one_and_exp_never_negative(self, current_level, current_exp, removed_exp):
        new_level, new_exp = GameLogic.calc_level_down(current_level, current_exp, removed_exp)
        assert new_level >= 1
        assert new_exp >= 0

    def test_cancel_is_inverse_of_completion_at_boundary(self):
        """レベル1でexpを加算してから同量を減算すると元の状態に戻ること"""
        new_level, new_exp, _ = GameLogic.calc_level_progress(1, 50, 30)
        back_level, back_exp = GameLogic.calc_level_down(new_level, new_exp, 30)
        assert back_level == 1
        assert back_exp == 50


class TestCalculateMaxHp:
    @given(level=st.integers(min_value=1, max_value=200))
    def test_always_positive_and_monotonic(self, level):
        assert GameLogic.calculate_max_hp(level) > 0
        assert GameLogic.calculate_max_hp(level + 1) > GameLogic.calculate_max_hp(level)
