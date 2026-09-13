"""デイリールーティン(すごろく形式の生活導線UI)のフロー定義。

quest_master とは異なり、この内容は療育目的で固定された生活動線そのものであり
親が編集する対象ではないため、DBテーブルではなくこの定数として持つ
(migrations/README.md・0010_add_routine_progress.sql参照)。

Days Key: 0=月, 1=火, 2=水, 3=木, 4=金, 5=土, 6=日 (quest_data.pyと同じ規約)
"""
import datetime
from typing import List, Optional, Tuple, TypedDict


class RoutineStep(TypedDict):
    key: str
    label: str
    icon_key: str
    checkpoint_time: Optional[str]  # 'HH:MM' 形式。設定されたステップだけが強制切替の対象
    # 土日はチェックポイント時刻だけ変える(要件: フロー構成・ステップは平日と揃える)。
    # Noneなら平日と同じcheckpoint_timeをそのまま使う。
    weekend_checkpoint_time: Optional[str]
    # Trueなら土日はこのステップ自体をスキップする(要件: 休日PMはおやつ休憩から開始)。
    weekend_skip: bool
    # Trueならこのステップは日付をまたいで引き継ぐ(要件: 宿題は金曜終わっていれば
    # 土日は不要、土曜終わっていれば日曜は不要)。判定はroutine_service側で行う。
    weekend_carryover: bool
    # Trueならこのステップは同じフロー内の他のchecklist=Trueなステップと合わせて
    # 順不同でチェックできる「チェックリスト」グループの一員になる(要件: 朝の準備・
    # 寝る準備は順番を強制せず好きな順にチェックしたい)。フロー内でchecklist=Trueな
    # ステップは連続する一塊のみを想定している(get_checklist_range参照)。この塊は
    # フロー先頭(例: amの朝の準備5項目)・チェックポイント通過後(例: pmの寝る準備4項目)
    # のどちらにも置けるが、routine_service側はシーケンシャルな進行が塊の先頭indexに
    # 到達した時点で塊全体を一括'current'にする、という単一の仕組みで両対応している。
    checklist: bool


class RoutineFlow(TypedDict):
    title: str
    day_of_week: List[int]
    start_trigger_time: str  # 'HH:MM'。この時刻を過ぎると当日分の進捗が開始する
    steps: List[RoutineStep]


ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]
WEEKEND_DAYS = {5, 6}

# フロー完走ボーナスの満額 (Youtube 30分チケット(quest_data.py REWARDS id=11)と同額)。
# チェックポイント通過時、チェックポイントより前のステップの達成率に応じて按分する。
FULL_BONUS_GOLD = 150
FULL_BONUS_EXP = 30

ROUTINE_FLOWS: dict[str, RoutineFlow] = {
    'am': {
        'title': '起きてから出発まで',
        # 土日も同じフローを使う(要件: なるべく平日と揃える)。チェックポイント時刻だけ
        # 'free'ステップのweekend_checkpoint_timeで土日用に上書きする。
        'day_of_week': ALL_DAYS,
        # 朝5:00を過ぎたら当日分のすごろくを開始する(それより前にアプリを開いても
        # 前日分の状態のまま)。土日も同じ(要件確認済み)。
        'start_trigger_time': '05:00',
        'steps': [
            # 朝の準備5項目は順番を強制しないチェックリスト(要件確認済み: 表示順は
            # 固定するが実施順は問わない)。5項目均等割りだと満額150Gold/30EXPを
            # ぴったり割り切れる(1項目=30Gold/6EXP相当)。
            {'key': 'meal', 'label': '朝ごはん', 'icon_key': 'meal', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
            {'key': 'clothes', 'label': '着替える', 'icon_key': 'clothes', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
            {'key': 'wash', 'label': '顔を洗う', 'icon_key': 'wash', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
            {'key': 'teeth', 'label': '歯磨き', 'icon_key': 'teeth', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
            {'key': 'toilet', 'label': 'トイレ', 'icon_key': 'toilet', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
            # 土日は学校が無いため、出発(チェックポイント通過)の締切を09:30に後ろ倒しする。
            {'key': 'free', 'label': '自由時間', 'icon_key': 'free', 'checkpoint_time': '07:50', 'weekend_checkpoint_time': '09:30', 'weekend_skip': False, 'weekend_carryover': False, 'checklist': False},
            {'key': 'leave', 'label': '出発', 'icon_key': 'leave', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': False},
        ],
    },
    'pm': {
        'title': '帰ってから寝るまで',
        # 土日も同じフローを使う(要件: なるべく平日と揃える)。土日は先頭の
        # handwashだけスキップし、おやつ休憩から開始する(要件確認済み)。
        'day_of_week': ALL_DAYS,
        # 実際の下校/帰宅時刻に合わせてユーザーが確定した値。土日も同じ14:00。
        'start_trigger_time': '14:00',
        'steps': [
            # 土日は手洗い・うがいをスキップし、おやつ休憩からスタートする(要件確認済み)。
            {'key': 'handwash', 'label': '手洗い・うがい', 'icon_key': 'handwash', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': True, 'weekend_carryover': False, 'checklist': False},
            {'key': 'snack', 'label': 'おやつ休憩', 'icon_key': 'snack', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': False},
            # 金曜に完了していれば土日は不要、土曜に完了していれば日曜は不要
            # (要件確認済み)。判定はroutine_service._resolve_skip_keysが行う。
            {'key': 'homework', 'label': '宿題', 'icon_key': 'homework', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': True, 'checklist': False},
            # 明日の準備も宿題と同じ繰越ルール(要件確認済み: 金曜/土曜に完了していれば
            # 以降の土日は不要)。
            {'key': 'tomorrow_prep', 'label': '明日の準備', 'icon_key': 'tomorrow_prep', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': True, 'checklist': False},
            # 自由時間→寝る準備の締切を18:00に変更(要件確認済み)。土日も平日と同じ
            # 18:00のまま(要件確認済み: 現状維持)。
            {'key': 'free', 'label': '自由時間', 'icon_key': 'free', 'checkpoint_time': '18:00', 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': False},
            # 寝る準備4項目は朝の準備と同様、順番を強制しないチェックリスト
            # (要件確認済み)。チェックポイント(free)通過後に一括で'current'になる。
            {'key': 'dinner', 'label': '晩ごはん', 'icon_key': 'meal', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
            {'key': 'bath', 'label': 'お風呂', 'icon_key': 'bath', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
            {'key': 'nightclothes', 'label': '着替え', 'icon_key': 'clothes', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
            {'key': 'nightteeth', 'label': '歯磨き', 'icon_key': 'teeth', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
            {'key': 'sleep', 'label': '就寝', 'icon_key': 'sleep', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': False},
        ],
    },
}


def get_checkpoint_index(flow: RoutineFlow) -> Optional[int]:
    """フロー内でチェックポイント(強制切替の境界)を持つステップのインデックスを返す。"""
    for idx, step in enumerate(flow['steps']):
        if step['checkpoint_time']:
            return idx
    return None


def get_checklist_range(flow: RoutineFlow) -> Optional[Tuple[int, int]]:
    """フロー内のchecklist=Trueな連続ブロックの(開始index, 終了index+1)を返す。

    checklist=Trueなステップが無ければNone。ブロックはフロー先頭(am)・
    チェックポイント通過後(pm)のどちらにも置けるが、単一の連続ブロックのみを
    想定している(routine_service側の初期化・トグル処理もこの前提で書かれている)。
    """
    indices = [idx for idx, step in enumerate(flow['steps']) if step['checklist']]
    if not indices:
        return None
    return indices[0], indices[-1] + 1


def get_effective_checkpoint_time(step: RoutineStep, now: datetime.datetime) -> Optional[str]:
    """`now`の曜日に応じた、そのステップの実際のチェックポイント締切時刻を返す。

    土日(weekend_checkpoint_time)の上書きが無ければ平日のcheckpoint_timeをそのまま使う。
    """
    if now.weekday() in WEEKEND_DAYS and step['weekend_checkpoint_time']:
        return step['weekend_checkpoint_time']
    return step['checkpoint_time']
