"""デイリールーティン(すごろく形式の生活導線UI)のフロー定義。

quest_master とは異なり、この内容は療育目的で固定された生活動線そのものであり
親が編集する対象ではないため、DBテーブルではなくこの定数として持つ
(migrations/README.md・0010_add_routine_progress.sql参照)。

Days Key: 0=月, 1=火, 2=水, 3=木, 4=金, 5=土, 6=日 (quest_data.pyと同じ規約)
"""
from typing import List, Optional, TypedDict


class RoutineStep(TypedDict):
    key: str
    label: str
    icon_key: str
    checkpoint_time: Optional[str]  # 'HH:MM' 形式。設定されたステップだけが強制切替の対象


class RoutineFlow(TypedDict):
    title: str
    day_of_week: List[int]
    start_trigger_time: str  # 'HH:MM'。この時刻を過ぎると当日分の進捗が開始する
    steps: List[RoutineStep]


WEEKDAYS = [0, 1, 2, 3, 4]

# フロー完走ボーナスの満額 (Youtube 30分チケット(quest_data.py REWARDS id=11)と同額)。
# チェックポイント通過時、チェックポイントより前のステップの達成率に応じて按分する。
FULL_BONUS_GOLD = 150
FULL_BONUS_EXP = 30

ROUTINE_FLOWS: dict[str, RoutineFlow] = {
    'am': {
        'title': '起きてから出発まで',
        'day_of_week': WEEKDAYS,
        # 朝5:00を過ぎたら当日分のすごろくを開始する(それより前にアプリを開いても
        # 前日分の状態のまま)。
        'start_trigger_time': '05:00',
        'steps': [
            {'key': 'wash', 'label': '顔を洗う', 'icon_key': 'wash', 'checkpoint_time': None},
            {'key': 'meal', 'label': '朝ごはん', 'icon_key': 'meal', 'checkpoint_time': None},
            {'key': 'clothes', 'label': '着替える', 'icon_key': 'clothes', 'checkpoint_time': None},
            {'key': 'teeth', 'label': '歯磨き', 'icon_key': 'teeth', 'checkpoint_time': None},
            {'key': 'free', 'label': '自由時間', 'icon_key': 'free', 'checkpoint_time': '07:50'},
            {'key': 'leave', 'label': '出発', 'icon_key': 'leave', 'checkpoint_time': None},
        ],
    },
    'pm': {
        'title': '帰ってから寝るまで',
        'day_of_week': WEEKDAYS,
        # 仮の既定値。実際の下校/帰宅時刻に合わせて要調整(ユーザー確認事項)。
        'start_trigger_time': '15:00',
        'steps': [
            {'key': 'handwash', 'label': '手洗い・うがい', 'icon_key': 'handwash', 'checkpoint_time': None},
            {'key': 'snack', 'label': 'おやつ休憩', 'icon_key': 'snack', 'checkpoint_time': None},
            {'key': 'homework', 'label': '宿題', 'icon_key': 'homework', 'checkpoint_time': None},
            {'key': 'free', 'label': '自由時間', 'icon_key': 'free', 'checkpoint_time': '20:00'},
            {'key': 'nightprep', 'label': '寝る準備', 'icon_key': 'nightprep', 'checkpoint_time': None},
            {'key': 'sleep', 'label': '就寝', 'icon_key': 'sleep', 'checkpoint_time': None},
        ],
    },
}


def get_checkpoint_index(flow: RoutineFlow) -> Optional[int]:
    """フロー内でチェックポイント(強制切替の境界)を持つステップのインデックスを返す。"""
    for idx, step in enumerate(flow['steps']):
        if step['checkpoint_time']:
            return idx
    return None
