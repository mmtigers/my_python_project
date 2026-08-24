# NAS書き込み権限チェック タイムアウト調査報告 (2026-08-24)

対象: `nas_monitor.py`(`check_write_permission`)が2026-08-24 00:00〜07:00の毎時チェック8回全てで
「Write permission check timed out after 5s (NAS mount possibly stalled)」を出力した事象。

依頼元: 引き継ぎプロンプト①(NAS本体側のログ/リソース調査)

---

## 0. 調査範囲と制約(最初に明記)

本セッションはこのGitリポジトリ(`mmtigers/my_python_project`)専用の隔離されたクラウド実行環境で動作しており、
自宅LAN(`192.168.1.20` のBUFFALO LS720D実機、および実行元のRaspberry Pi実機)へのネットワーク経路を持たない。
そのため、依頼にあった以下はこのセッションから**直接実行できない**:

- NAS本体の syslog / dmesg の確認
- Samba/NFSデーモンのログ確認
- NAS本体のCPU使用率・ディスクI/O・RAID状態(リビルド/スクラブ中か)の確認
- 同時間帯の他クライアントからのアクセス遅延有無の確認
- `/mnt/nas/home_system/assets` の実際のパーミッション/ACLの確認(マウント経由の実地確認)

代わりに、本リポジトリのソースコード(クライアント側の実装)を精査し、`FACT`と突き合わせることで、
**コードから確実に言えること**と、**実機でしか確認できないこと**を切り分けた。後者については、
実機(Raspberry Pi / NAS)で実行すべき具体的なコマンドを「6. 実機での確認手順」にまとめてある。
ユーザー側で実行し、結果を共有してもらえれば追加で分析する。

---

## 1. コード調査で判明した事実

### 1.1 `nas_monitor.py` の書き込み権限チェック(タイムアウトを出しているのはここ)

`MY_HOME_SYSTEM/monitors/nas_monitor.py:79-105` の `check_write_permission()`:

```python
test_file = os.path.join(self.mount_point, '.write_test')   # /mnt/nas/.write_test
script = (
    "import os, sys\n"
    "path = sys.argv[1]\n"
    "with open(path, 'w') as f:\n"
    "    f.write('health_check')\n"
    "os.remove(path)\n"
)
subprocess.run(
    [sys.executable, "-c", script, test_file],
    timeout=self.timeout,   # config.NAS_CHECK_TIMEOUT = 5秒固定
    check=True,
    capture_output=True,
)
```

重要な特徴:

- **毎回、新しいPythonインタプリタをsubprocessとして起動**し、その中で open→write→close→remove を行う。
  CIFSマウントがストールした場合に監視プロセス本体が巻き込まれてハングしないための設計(過去のコミット
  `a8166a3 fix(M-4-6): NAS書き込み権限チェックがストール時にkillできずハングする問題を修正` で導入)。
- タイムアウトは `config.NAS_CHECK_TIMEOUT = 5`(秒)固定、**リトライは一切ない**。5秒以内に
  「Pythonインタプリタ起動 + open + write + close + remove」の全てが完了しないと即座にkillされ、ERRORとして
  `is_currently_healthy = False` 扱いになる(`nas_monitor.py:247`)。
- 対象パスは `/mnt/nas/.write_test`(マウントポイント直下)であり、`assets/.write_test` ではない。
  FACTにある `assets/.write_test` は後述の `backup_service.py` 起動時の別チェックのパス。

### 1.2 `backup_service.py` 起動時に成功している方の書き込みチェック(バックオフあり)

FACTにある「`.write_test`への4回連続ENOENT失敗→1s/2s/4s/8sバックオフ後に成功」は、`nas_monitor.py`の
チェックとは**別の実装**である。`backup_service.py` は起動時に `import config` を実行し、これが
`MY_HOME_SYSTEM/config.py:226-229` の

```python
ASSETS_DIR: str = ensure_safe_path_with_backoff(
    os.path.join(NAS_PROJECT_ROOT, "assets"),
    ...
)
```

を評価する。`ensure_safe_path_with_backoff` は `verify_and_initialize_storage`(`config.py:40-89`)に委譲し、
そこで実際に `assets/.write_test` への書き込みテストを行っている:

```python
test_file = os.path.join(base_path, ".write_test")   # /mnt/nas/home_system/assets/.write_test
for attempt in range(max_retries + 1):   # max_retries=5 (デフォルト)
    try:
        os.makedirs(base_path, exist_ok=True)
        with open(test_file, 'w') as f:
            f.write("test")
        os.remove(test_file)
        return True
    except (OSError, PermissionError, IOError) as e:
        wait_time = 2 ** attempt   # 1s, 2s, 4s, 8s, 16s
        time.sleep(wait_time)
```

こちらは**プロセス内で直接**(subprocess経由でなく)I/Oを行い、失敗しても最大5回・累計最大31秒
(1+2+4+8+16)まで指数バックオフでリトライする。FACTの「4回失敗→バックオフ後に成功」は、この関数が
attempt 0〜3で失敗し、attempt 4(累計待機 1+2+4+8=15秒経過後)で成功したケースに一致する。

**同一マウントに対する2つの独立したチェックが、片方は単発5秒厳格、片方は最大31秒まで粘るリトライ付き、
という非対称な実装になっている。** これがFACTに書かれた「軽い操作(権限チェック)は毎回タイムアウトする一方、
重い操作(ファイル転送)は成功する」という非対称性の"重い/軽い"という理解は、コードの実態としては不正確で、
正しくは**「リトライ予算の差」**である。バックアップ転送自体(`shutil.copy2`、83.33MB)には独自のタイムアウトも
リトライも無い(`backup_service.py:60`)ため、転送が成功しているのは「起動時のストレージ初期化チェックが
バックオフの末に成功し、マウントが安定した後に転送を実行しているから」であり、転送処理自体が
「重いから通る」という因果関係ではない。

### 1.3 実行スケジュール(「毎時00分台」に集中する理由はスケジューラの仕様通り)

`MY_HOME_SYSTEM/scheduler_boot.py:29-43` で `nas_monitor.py` は `interval: 3600`(1時間に1回)としてのみ
登録されており、他に1時間おきのタスクは無い。したがって「8回中8回すべて毎時00分台に発生」という観測は、
**そもそも `nas_monitor.py` がその時間帯にしか実行されていないことの帰結であり、NAS側で毎時00分台に
何らかの周期イベントが起きている証拠にはならない**(観測頻度がそこにしか存在しないだけ)。
本当に注目すべきは「時間帯」ではなく「**8回中8回、100%失敗している**」という再現性の高さである。

---

## 2. 依頼された5項目への回答

| # | 依頼内容 | 本調査での回答 |
| --- | --- | --- |
| 1 | NAS本体のsyslog/dmesgとの突き合わせ | **実機アクセス不可のため未実施。** 6章のコマンドで取得・共有してほしい。 |
| 2 | Samba/NFSデーモンログのエラー・再接続確認 | **実機アクセス不可のため未実施。** ただし1.1の通り、クライアント側は5秒という短い予算で毎回新規プロセスを起動しI/Oしており、SMBの再接続・oplockブレイク待ち等が5秒を超えると素直に説明がつく症状。6章で確認方法を提示。 |
| 3 | NAS本体のCPU/ディスクI/O/RAID状態(リビルド/スクラブ中か) | **実機アクセス不可のため未実施。** BUFFALO LS720Dはミラーリング(RAID1)構成が一般的で、Web管理画面(`http://192.168.1.20/`)の「システム」→「RAIDアレイ情報」、または対応していればSSH経由の `cat /proc/mdstat` で確認可能。6章参照。 |
| 4 | 同時間帯の他クライアントの遅延有無 | **実機アクセス不可のため未実施。** ただしコード上、この期間に同一Pi上でNASへアクセスしているのは `backup_service.py`(04:00台起動時のみ)と `nas_monitor.py`(毎時)だけで、他の常時稼働サービス(camera_service等)がNASへ継続アクセスしている形跡は`config.py`のパス定義からは見当たらない(後述4章で仮説として言及)。他クライアント(PC等)からのアクセス有無はNAS側ログでしか確認できない。 |
| 5 | `assets`ディレクトリのパーミッション/ACL異常の可能性 | **コード上は排除できる。** FACTの「4回連続ENOENT」は`verify_and_initialize_storage`が`os.makedirs`直後に`open()`する際の一時的な失敗であり、`except`節は`(OSError, PermissionError, IOError)`を一括で握りつぶしてリトライしているため、実際のエラー種別(ENOENT=マウント未確立か、EACCES=権限不足か)がログに出ていても本文中では区別されていない。ただし最終的に成功している(5回目のattemptで通っている)ため、恒常的な権限/ACLの誤設定であれば1回目も5回目も同じ理由で失敗し続けるはずで、「リトライで通る」という挙動そのものが権限問題ではなく**一時的なマウント/応答遅延**であることを強く示唆する。恒常的な権限問題であれば`nas_monitor`側も同様に"リトライすれば通る"はずが無く、`backup_service`と挙動が一致しないはずである。 |

---

## 3. 根本原因の切り分け:NAS側 vs クライアント側

現時点でコードとFACTのみから立てられる、最も説明力の高い仮説は次の2つ。優先順位順に記載する。

### 仮説A(有力): NAS(BUFFALO LS720D)のHDD自動スピンダウンによる、アクセス再開時の起床遅延

根拠:
- `nas_monitor.py` はNASへ**1時間に1回しかアクセスしない**設計になっている(1.3)。もし他にNASへ定常アクセスする
  プロセスが無ければ、LS720Dのデフォルト設定(工場出荷時は「一定時間アクセスが無いとHDDを自動停止」が
  有効なことが多い)により、チェックの直前にHDDがスタンバイ(スピンダウン)している可能性が高い。
- コンシューマ向けHDDのスピンアップには一般に5〜15秒程度かかる。`nas_monitor`の5秒固定タイムアウトは
  この起床時間より短く、**リトライも無い**ため、スピンダウンしていれば理屈上ほぼ確実に(=観測通り8/8で)
  タイムアウトする。
- 一方 `backup_service.py`(`config.py`のストレージ初期化)は最大31秒まで指数バックオフで待つため、
  スピンアップが完了するまで粘って成功できる。両者が同じ根本原因(起床待ち)に対して異なる結果になる、
  という非対称性を矛盾なく説明できる。
- 「毎回100%失敗」という再現性の高さも、確率的な輻輳やネットワーク瞬断よりも、**周期的・機械的に起きる
  現象**(スピンダウン→次回アクセスで必ず遅延)の方が説明として自然。

検証方法(実機で確認可能・6章参照): LS720DのWeb管理画面でHDD電源管理設定を確認する。また、
`nas_monitor`実行前後でNAS側のディスクアクティビティLED挙動やsyslogの `ata` / `hd-idle` 関連ログを見る。

### 仮説B(補助的): Raspberry Pi側のsubprocess起動オーバーヘッドがタイムアウト予算を圧迫している

`check_write_permission()` は5秒の予算の中で「新規Pythonインタプリタの起動」も行っている。Raspberry Pi
(特に仮想環境`.venv`経由、かつ他の1時間間隔ではない5〜10分間隔タスクとThreadPoolExecutorで並行実行されている
状態)では、CPU負荷次第でインタプリタ起動だけで数百ms〜数秒を消費し得る。これは仮説Aと排他ではなく、
**両方が重なって5秒という予算をさらに厳しくしている**可能性がある。ただし「8/8で常に失敗」を単独で
説明するには弱く(Pi側の負荷は時刻に対して一定ではないはずなので)、主因は仮説Aとみている。

### 排除できる/優先度が低い仮説

- **恒常的な権限・ACL異常(依頼5)**: 2章で述べた通り、リトライで成功している事実と矛盾するため優先度低。
- **NAS側RAIDリビルド/スクラブの常時実行**: もしこれが原因なら`backup_service`の83MB転送のような重い
  I/Oも同程度以上に遅延するはずだが、転送自体は成功している。可能性はゼロではないが、リビルド/スクラブは
  通常一過性(進行中は継続的に性能劣化する)であり、「毎時00分台だけ」「軽い操作だけ」失敗するパターンとは
  整合しにくい。実機での確認(6章)で最終的に排除・確認する。
- **NAS側の毎時cronジョブ**: 1.3の通り、観測が「毎時00分台に集中している」こと自体はクライアント側の
  スケジュール仕様で説明がつくため、これを根拠にNAS側の毎時ジョブを推定するのは早計。実機のsyslog/samba
  ログで実際に毎時00分台にNAS側イベントがあるかどうかを見て初めて判断できる。

---

## 4. 恒久対策の提案

### 4.1 すぐに効く暫定対策(クライアント側コード変更・低リスク)

1. **`check_write_permission()` にもリトライ/バックオフを入れ、`backup_service.py`側の
   `verify_and_initialize_storage` と同程度の予算(例: 初回5秒 + 失敗時のみ追加で2回程度リトライ)にする。**
   これにより「HDDスピンアップ待ちだけで毎回フォールバックに落ちる」誤検知を防げる。
   ただし本来の目的(CIFSストール検知でプロセスがハングしない)を壊さないよう、リトライも
   subprocess単位のtimeoutを維持したまま行う必要がある。
2. **`NAS_CHECK_TIMEOUT`(現在5秒固定)を、実測したHDDスピンアップ時間 + マージンに合わせて調整する。**
   ただしこれだけだと「本当にNASが落ちている」ケースの検知も遅れるため、(1)のリトライ方式の方が望ましい。
3. どちらを採る場合も、タイムアウト発生時のログに「起床待ちで失敗したのか」「本当に無応答なのか」を
   区別できるよう、失敗時に軽いping/mount確認を添えて記録すると次回以降の切り分けが楽になる。

### 4.2 NAS本体側の恒久対策(要実機作業・仮説A確認後)

1. **LS720DのHDD自動スピンダウン(パワーセービング)設定を無効化する**、または `nas_monitor` の監視間隔を
   スピンダウン閾値より短くして「常にディスクを起こしておく」。前者は消費電力・ディスク寿命とのトレードオフ、
   後者は監視間隔を短くする分チェック頻度が増える。24時間稼働の監視用途なら、一般的には
   スピンダウン無効化(または長めの閾値)の方が誤検知を減らせる。
2. 監視自体に軽量な定期keep-alive(例: 10〜15分おきに軽いstat程度のアクセス)を別途入れ、書き込みテストの
   前にディスクを起こしておく。
3. RAID状態・SMARTの定期監視をNAS側の標準機能(あれば)またはPi側から `smartctl`/管理画面API等で
   自動チェックし、リビルド/スクラブ中は書き込みチェックのアラートを抑制する運用にする。

### 4.3 監視・切り分けの改善(構造的対策)

- `nas_monitor.py` と `backup_service.py`(`config.py`)が同じ対象(NASマウント)に対して異なる
  タイムアウト/リトライポリシーを個別に実装しているのは重複かつ非対称の原因になっている。
  将来的には「NASへの安全なI/O」を1箇所(例: `core/nas_utils.py`)に共通化し、ポリシー(タイムアウト値・
  リトライ回数)を一元管理する方が、今回のような非対称による誤検知を構造的に防げる。

---

## 5. まとめ

- **クライアント側コードの精査により**、タイムアウトが出ている`nas_monitor.py`のチェックと成功している
  `backup_service.py`のチェックは、同じNASマウントに対する**別実装・別リトライポリシー**であることが判明した。
  「軽い操作だから失敗、重い操作だから成功」ではなく、「リトライ予算が5秒しかないか、最大31秒あるか」の差である。
- 最も説明力の高い仮説は、**LS720DのHDD自動スピンダウンによる起床遅延**に対して、`nas_monitor`側の
  タイムアウトが短くリトライも無いため、ほぼ毎回引っかかっているというもの。ただしこれは実機のNAS管理画面・
  syslog等で確認しないと確定できない。
- 依頼された5項目のうち、コードのみから確定的に答えられたのは **5番(パーミッション/ACL異常の可能性は低い)**
  のみで、1〜4番は実機側のログ・状態確認が必要。6章の手順で取得したものを共有してもらえれば、
  仮説A/Bの検証および恒久対策の絞り込みを次のステップとして行う。

---

## 6. 実機での確認手順(このセッションでは実行不可・ユーザー側で実行してほしい)

### 6.1 NAS(BUFFALO LS720D)側

- Web管理画面(`http://192.168.1.20/`):
  - 「システム」→「電源」: HDD自動スピンダウン(パワーセービング)設定の有無・閾値を確認
  - 「ストレージ」→「RAIDアレイ情報」: RAID状態(Normal/Rebuilding/Scrubbing等)を確認
  - 対応していれば「システムログ」画面で 2026-08-24 00:00〜07:00 の範囲、特に各時 00:00〜01:10 付近の
    イベントを確認
- SSHアクセスが可能な場合(LS720Dはacp_commander等でSSH有効化されていることがある):
  ```bash
  # syslog / samba ログの該当時間帯抽出
  grep -E "2026-08-24 (00|01|02|03|04|05|06|07):0[0-2]" /var/log/messages
  grep -E "2026-08-24 (00|01|02|03|04|05|06|07):0[0-2]" /var/log/samba/log.smbd
  # RAID状態
  cat /proc/mdstat
  # ディスクI/O・スピンダウン状態
  hdparm -C /dev/sda /dev/sdb   # STANDBY / ACTIVE/IDLE を確認
  iostat -x 1 5
  # 権限/ACL(問題の切り分け用)
  ls -la /mnt/array1/home_system/assets   # 実パスは環境に応じて読み替え
  getfacl /mnt/array1/home_system/assets
  ```

### 6.2 Raspberry Pi(クライアント)側

```bash
# NASマウント関連のカーネルログ(CIFS/NFSのタイムアウト・再接続警告)
dmesg -T | grep -iE "cifs|nfs|smb"
journalctl -k --since "2026-08-24 00:00" --until "2026-08-24 07:30" | grep -iE "cifs|nfs|smb"

# home_system.log 側で、当該時間帯のnas_monitor/backup_service以外のNASアクセスを横断確認
grep -E "2026-08-24 0[0-7]:" MY_HOME_SYSTEM/logs/home_system.log | grep -iE "nas|mnt"

# 実際にマウントされているオプション(vers, timeout系パラメータ等)を確認
mount | grep /mnt/nas
```

これらの出力を共有してもらえれば、4章の仮説A/Bのどちらが実際の原因かを確定し、
4.2の恒久対策(NAS側設定変更)の要否を判断できる。

---

## 7. 続報 (2026-08-24): PR #56 との突き合わせ

本報告書は PR #54 としてマージ済み。その後、別PR #56
「fix(nas): ENOENT/タイムアウト切り分け結果を踏まえNAS復旧待ちのリトライを追加」
(コミット `402dc48`) が独立に master へマージされており、4.1節で提案した対策と
重なる内容だったため、突き合わせた結果を記録する。

### 7.1 カバー済み

- **4.1-1 (`check_write_permission()`へのリトライ/バックオフ追加)**: ✅ カバー済み。
  `config.NAS_WRITE_CHECK_RETRIES`(デフォルト3、初回+2回リトライ=提案の「初回5秒+失敗時のみ追加で
  2回程度リトライ」と一致)を追加し、各attemptは従来通りsubprocessの`timeout=self.timeout`を維持した
  ままExponential Backoff(1s, 2s)で再試行するよう変更された(`nas_monitor.py:99-126`)。
  本来の目的(CIFSストール時にプロセスがハングしないこと)を壊さない設計になっている。
- 追加で `start_all.sh` のNASマウント確認(Phase 1)にも同種のリトライが入り、起動直後の
  automountタイミング競合を緩和している。これは本報告書では提案していなかったが、同じ根本原因
  (autofsの再トリガー遅延)に対する追加の対策として妥当。

### 7.2 未カバー(次のアクション候補)

- **4.1-3 (診断ログの追加)**: 元は未カバーだったため、本セッションで追記した。
  `check_write_permission()`が全リトライを使い切って異常確定する際、`check_ping()`/`check_mount()`
  の結果を `[diagnostic: ping=..., mount=...]` としてERRORログに残すようにした
  (`nas_monitor.py`、対応するテスト `test_final_timeout_logs_ping_and_mount_diagnostic` を追加)。
  ping/mountが両方OKで書き込みだけ失敗する場合はディスク起床待ち(仮説A)を、pingすら失敗する場合は
  ネットワーク/NAS本体側の障害を疑う材料になる。
- **4.3 (NAS I/Oリトライポリシーの一元化)**: 未カバーのまま。PR #56は `nas_monitor.py` 側の
  リトライ回数・待機時間を独自に実装しており、`config.py`の`verify_and_initialize_storage`とは
  今も別々のExponential Backoff実装が並存している(前者は3回・1s/2s、後者は最大5回・1s/2s/4s/8s/16s)。
  将来的に`core/nas_utils.py`等へ共通化する余地は残っている。実装の必要性・優先度は次のアクション時に
  ユーザーと相談のうえ判断すること。
- **細かい差異**: `check_write_permission()`のリトライは`subprocess.TimeoutExpired`発生時のみ働く。
  マウント未確立直後に`open()`が`FileNotFoundError`(ENOENT)を投げて`CalledProcessError`になるケースは
  リトライされず即座に失敗として扱われる(`nas_monitor.py:123-125`)。一方`config.py`側は
  `(OSError, PermissionError, IOError)`を包括的にリトライ対象にしている。今回の8/8失敗ログはいずれも
  「タイムアウト」であり「ENOENTでの即失敗」ではなかったため実害は確認されていないが、非対称性としては
  残っている。
- **4.2 (NAS本体側のスピンダウン設定変更)・仮説A/Bの確定**: 引き続き実機作業が必要。本セッションも
  自宅LANへの経路を持たない隔離環境のままであり、未着手。§6の手順を実機で実行し結果を持ち込んでほしい。
