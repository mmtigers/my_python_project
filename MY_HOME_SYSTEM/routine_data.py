"""デイリールーティン(すごろく形式の生活導線UI)のフロー定義。

quest_master とは異なり、この内容は療育目的で固定された生活動線そのものであり
親が編集する対象ではないため、DBテーブルではなくこの定数として持つ
(migrations/README.md・0010_add_routine_progress.sql参照)。

Days Key: 0=月, 1=火, 2=水, 3=木, 4=金, 5=土, 6=日 (quest_data.pyと同じ規約)
"""
import datetime
from typing import List, Optional, Tuple, TypedDict


class _RoutineStepBase(TypedDict):
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


class RoutineStep(_RoutineStepBase, total=False):
    """任意フィールド(省略時はそれぞれ 0 / False)。

    `weekday_skip` は `weekend_skip` の逆で、Trueなら平日はこのステップ自体を
    スキップする(要件: パパの平日の昼は「お仕事」だけにし、キッチン/リビング
    リセットは土日にだけ出す)。`weekend_skip` と同じく判定は routine_service 側
    (_resolve_skip_keys)で行う。必須フィールド側に置かなかったのは、既存の全ステップ
    リテラルを書き換えずに済ませるため。

    `gold`/`exp` はステップ個別の即時報酬。

    大人用フロー(ROUTINE_FLOW_SETS)で、生活動線そのものだったデイリークエスト
    (例: ママの「夕食を作る」)を quest_data.QUESTS から「すごろく」側へ寄せる
    ために使う。指定したステップを順番どおり完了報告した時点で、元のクエストと
    同額の gold/exp をその場で付与する(get_step_reward参照)。省略時は0で、
    子ども用フローの全ステップは従来どおりチェックポイント通過ボーナス
    (FULL_BONUS_GOLD/EXPの按分)だけを報酬とする。
    """
    gold: int
    exp: int
    weekday_skip: bool


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

# 子ども(智矢・涼花)用のフローセット。大人には別セットを出す(ROUTINE_FLOW_SETS・
# get_flow_set参照)。get_flow_setのフォールバック先でもあるため名前は据え置く。
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
            # （画面非表示化で変更）以前はこの後に単なる「出発」表示用ステップ(leave、
            # タップ操作以外の意味を持たない)が続いていたが、削除した。'free'がこの
            # フローで最後(かつ唯一)のchecklist=Trueブロック直後のステップのため、
            # _apply_forced_transitionが締切通過時にnext_active_indexへ渡すindexが
            # len(steps)と一致し、is_complete=Trueへ直接遷移するようになる(要件:
            # 7:50を過ぎたら朝の準備の画面自体を表示しない。isRoutineFlowBlocking/
            # isRoutineFlowFreeTimeは共にis_complete=Trueで false になるため、
            # フロントエンド側の変更は不要)。
            {'key': 'free', 'label': '自由時間', 'icon_key': 'free', 'checkpoint_time': '07:50', 'weekend_checkpoint_time': '09:30', 'weekend_skip': False, 'weekend_carryover': False, 'checklist': False},
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
            # 自由時間→寝る準備の締切は17:30(要件確認済み。以前は18:00だった)。
            # 土日も平日と同じ17:30のまま(要件確認済み: 現状維持)。
            {'key': 'free', 'label': '自由時間', 'icon_key': 'free', 'checkpoint_time': '17:30', 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': False},
            # 寝る準備4項目は朝の準備と同様、順番を強制しないチェックリスト
            # (要件確認済み)。チェックポイント(free)通過後に一括で'current'になる。
            {'key': 'dinner', 'label': '晩ごはん', 'icon_key': 'meal', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
            {'key': 'bath', 'label': 'お風呂', 'icon_key': 'bath', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
            {'key': 'nightclothes', 'label': '着替え', 'icon_key': 'clothes', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
            {'key': 'nightteeth', 'label': '歯磨き', 'icon_key': 'teeth', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
            # 明日の準備は「宿題の次」の一本道から寝る準備チェックリストへ移した
            # (要件確認済み: 智矢の「明日の準備は別枠にしてほしい」)。これにより
            # 自由時間(チェックポイント)に入る条件は手洗い・おやつ・宿題の3つだけになり、
            # 明日の準備は晩ごはん〜歯磨きと同じく順不同でチェックできる。繰越ルール
            # (金曜/土曜に完了していれば以降の土日は不要)は移設後もそのまま維持する。
            {'key': 'tomorrow_prep', 'label': '明日の準備', 'icon_key': 'tomorrow_prep', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': True, 'checklist': True},
            {'key': 'sleep', 'label': '就寝', 'icon_key': 'sleep', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': False},
        ],
    },
}


# ==========================================
# 大人用フロー (パパ・ママ)
# ==========================================
# 以前は ROUTINE_FLOWS(子ども用)を全ユーザーで共用していたため、パパ・ママの画面にも
# 「宿題」「明日の準備」といった子ども専用のステップがそのまま表示されていた(要件:
# これを無くしたい)。ただし単に非表示にすると、すごろく画面を持つのが子どもだけになり
# 「大変なのは自分だけ」という見え方になってしまうため(要件)、代わりに大人にも同じ
# 路線図UIの大人版フローを持たせて左右対称にする。
#
# 骨格(朝の準備チェックリスト→自由時間チェックポイント→帰宅後の一本道→自由時間
# チェックポイント→寝る準備チェックリスト→就寝)は子ども用と意図的に揃えてあり、
# 一本道の「宿題」「明日の準備」の位置に、その人の実際の生活動線が入る。
# 報酬を持つステップ(gold/exp)は、同じ作業のデイリークエストを quest_data.QUESTS から
# 退役させて移設したものであり、二重計上にならないよう対で管理する。

# 朝は子どもと全く同じ5項目・同じ締切にする(大人側に固有の朝の家事は無いため)。
_ADULT_AM_CHECKLIST: List[RoutineStep] = [
    {'key': 'meal', 'label': '朝ごはん', 'icon_key': 'meal', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
    {'key': 'clothes', 'label': '着替える', 'icon_key': 'clothes', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
    {'key': 'wash', 'label': '顔を洗う', 'icon_key': 'wash', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
    {'key': 'teeth', 'label': '歯磨き', 'icon_key': 'teeth', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
    {'key': 'toilet', 'label': 'トイレ', 'icon_key': 'toilet', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
]

# 寝る準備も子どもと同じ4項目。チェックポイント(17:30)通過後に一括で'current'になる。
_ADULT_PM_CHECKLIST: List[RoutineStep] = [
    {'key': 'dinner', 'label': '晩ごはん', 'icon_key': 'meal', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
    {'key': 'bath', 'label': 'お風呂', 'icon_key': 'bath', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
    {'key': 'nightclothes', 'label': '着替え', 'icon_key': 'clothes', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
    {'key': 'nightteeth', 'label': '歯磨き', 'icon_key': 'teeth', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': True},
]


def _build_adult_flows(pm_path_steps: List[RoutineStep]) -> dict[str, RoutineFlow]:
    """大人1人分のam/pmフローを組み立てる。

    `pm_path_steps` は pm フローのチェックポイント(自由時間)より前の一本道を
    まるごと渡す。子ども用フローの「手洗い・うがい→おやつ休憩→宿題→明日の準備」に
    相当する区間で、その人の生活動線によって中身も並びも異なるため
    (パパは14:00時点でまだ勤務中なので先頭が「お仕事」、ママは帰宅済みなので
    「帰宅・手洗い」から始まる)、共通の前置きは持たせず呼び出し側に任せている。

    ここに置いたステップはチェックポイント(17:30)より前にあるため、出発ボーナスの
    按分対象になる(_eligible_done_ratio)。
    """
    return {
        'am': {
            'title': '起きてから出発まで',
            'day_of_week': ALL_DAYS,
            'start_trigger_time': '05:00',
            'steps': [*_ADULT_AM_CHECKLIST, {
                'key': 'free', 'label': '自由時間', 'icon_key': 'free',
                'checkpoint_time': '07:50', 'weekend_checkpoint_time': '09:30',
                'weekend_skip': False, 'weekend_carryover': False, 'checklist': False,
            }],
        },
        'pm': {
            'title': '帰ってから寝るまで',
            'day_of_week': ALL_DAYS,
            'start_trigger_time': '14:00',
            'steps': [
                *pm_path_steps,
                {'key': 'free', 'label': '自由時間', 'icon_key': 'free', 'checkpoint_time': '17:30', 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': False},
                *_ADULT_PM_CHECKLIST,
                {'key': 'sleep', 'label': '就寝', 'icon_key': 'sleep', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': False},
            ],
        },
    }


# パパ: 平日の昼は「お仕事」だけ(要件確認済み)。pmフローは14:00開始で、その時刻は
# まだ勤務中のため一本道の先頭に置く。報酬は既存デイリークエスト id=10「会社勤務
# (通常)」がそのまま持ち続けるため、このステップ自体は gold/exp を持たない
# (二重計上を避ける)。「帰宅・手洗い」「ひと休み」は置かない。
#
# 土日は勤務が無いので「お仕事」をスキップするが、それだけだとチェックポイントより
# 前のステップが0個になり、何もせずに満額ボーナスが入ってしまう(要件確認済み: 土日は
# 別のステップを出す)。そこで旧デイリークエスト id=12「キッチンリセット」・id=13
# 「リビングリセット」(どちらも土日のみ、exp80/gold50)を quest_data.QUESTS から
# 退役させてここへ移設し、weekday_skip=True で土日だけ出るようにした。
DAD_ROUTINE_FLOWS = _build_adult_flows([
    {'key': 'work', 'label': 'お仕事', 'icon_key': 'work', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': True, 'weekend_carryover': False, 'checklist': False},
    {'key': 'kitchen_reset', 'label': 'キッチンリセット', 'icon_key': 'kitchen', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': False, 'weekday_skip': True, 'gold': 50, 'exp': 80},
    {'key': 'living_reset', 'label': 'リビングリセット', 'icon_key': 'living', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': False, 'weekday_skip': True, 'gold': 50, 'exp': 80},
])

# ママ: 旧デイリークエスト id=21「夕食を作る」(exp150/gold150)を quest_data.QUESTS
# から退役させてここへ移設した(要件確認済み)。毎日行うため weekend_skip=False で、
# 元クエストに 'days' 指定が無かったことに対応する。昼食を作る(id=20)・ゴミ捨て
# (id=1000〜1002)・日中の家庭運営(id=23)は、曜日や時間帯で内容が変わるため
# デイリークエストのまま残す(すごろくには載せない)。
MOM_ROUTINE_FLOWS = _build_adult_flows([
    # 子ども用と同じく、土日は「帰宅・手洗い」をスキップしてひと休みから始める。
    {'key': 'handwash', 'label': '帰宅・手洗い', 'icon_key': 'handwash', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': True, 'weekend_carryover': False, 'checklist': False},
    {'key': 'snack', 'label': 'ひと休み', 'icon_key': 'snack', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': False},
    {'key': 'cook_dinner', 'label': '夕食を作る', 'icon_key': 'kitchen', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': False, 'gold': 150, 'exp': 150},
])

# 上記2人以外の大人(将来ユーザーが増えた場合)向け。固有タスクを持たない素の骨格。
ADULT_DEFAULT_ROUTINE_FLOWS = _build_adult_flows([
    {'key': 'handwash', 'label': '帰宅・手洗い', 'icon_key': 'handwash', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': True, 'weekend_carryover': False, 'checklist': False},
    {'key': 'snack', 'label': 'ひと休み', 'icon_key': 'snack', 'checkpoint_time': None, 'weekend_checkpoint_time': None, 'weekend_skip': False, 'weekend_carryover': False, 'checklist': False},
])

# user_id → その人のフローセット。ここに無いユーザーは role で解決する(get_flow_set)。
ROUTINE_FLOW_SETS: dict[str, dict[str, RoutineFlow]] = {
    'dad': DAD_ROUTINE_FLOWS,
    'mom': MOM_ROUTINE_FLOWS,
}

ROLE_ADULT = 'role_adult'


def get_flow_set(user_id: str, role: Optional[str]) -> dict[str, RoutineFlow]:
    """そのユーザーに出すべきフローセット(flow_key -> RoutineFlow)を返す。

    user_id 固有の定義を最優先し、無ければ role で振り分ける。role が未設定
    (quest_users.role が NULL の旧データ等)の場合は従来どおり子ども用フローに
    フォールバックする。
    """
    if user_id in ROUTINE_FLOW_SETS:
        return ROUTINE_FLOW_SETS[user_id]
    if role == ROLE_ADULT:
        return ADULT_DEFAULT_ROUTINE_FLOWS
    return ROUTINE_FLOWS


def get_step_reward(step: RoutineStep) -> Tuple[int, int]:
    """ステップ個別の即時報酬 (gold, exp) を返す。未設定なら (0, 0)。"""
    return step.get('gold', 0), step.get('exp', 0)


def is_weekday_skip(step: RoutineStep) -> bool:
    """平日はこのステップをスキップするか(任意フィールド、未設定ならFalse)。"""
    return step.get('weekday_skip', False)


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
