# 単身赴任先PC(Windows)での batch_download_discord.py 実行環境構築

## 背景・目的

これまでの構成:

```
単身赴任先 → (インターネット経由) → 自宅のラズパイ → NASへ録画保存
単身赴任先 → (インターネット経由) → NASの映像を再生 ← カクつく/止まる
```

映像を「単身赴任先→自宅」で2往復（保存時と再生時）させているため、再生がネットワーク品質に
引きずられて不安定になる。これを

```
単身赴任先のPC → 単身赴任先のPCに接続した外付けHDDへ直接保存・再生
```

に変更し、ネットワークを経由しないようにする。

## 前提

- 単身赴任先のPCはWindows。
- `batch_download_discord.py`は`fcntl`（多重起動防止のファイルロック）や`signal.SIGTERM`など
  POSIX前提のAPIを使っているため、Windows上のPython(cmd/PowerShellから直接`python.exe`で実行)
  では動かない。**WSL2 (Windows Subsystem for Linux 2)** 上のUbuntuで実行する。
- 外付けHDDはWindows側でドライブレター（例: `E:`）が割り当てられている状態。WSL2は
  Windowsがマウント済みのドライブを自動的に`/mnt/<ドライブ文字の小文字>`配下に見せてくれる
  （例: `E:\` → `/mnt/e/`）ので、WSL側で別途マウント作業は不要。

## 1. リポジトリ側の変更点（このPRで対応済み）

単独のPC（NASなし・MY_HOME_SYSTEM本体なし）で動かすために、`batch_download_discord.py`に
以下の2点を追加した。

1. **NASマウント必須チェックを無効化できる環境変数を追加**
   従来は起動時に`/mnt/nas`配下がマウントされているかを必ず確認しており、NASが無い環境では
   常に`⛔ CRITICAL: NASマウントエラー`で処理が中断していた。
   環境変数 `DDD_REQUIRE_NAS_MOUNT=false` を設定するとこのチェック自体をスキップする
   （未設定時は従来通り`true`＝自宅ラズパイ側の挙動は変わらない）。

2. **MY_HOME_SYSTEM無しでもDiscord通知を送れる簡易フォールバックを追加**
   従来、`services.notification_service`（LINE Bot SDK・`config.py`・DB接続一式が必要）が
   見つからない場合は通知が完全に無効化されていた。単独環境ではこの一式を持ち込む必要はなく、
   環境変数 `DISCORD_WEBHOOK_NOTIFY` / `DISCORD_WEBHOOK_ERROR`（どちらも未設定なら
   `DISCORD_WEBHOOK_URL`）を設定するだけで、追加の依存無しにDiscordへテキスト通知が届く
   ようになった（すべて未設定なら、これまで通り通知は送られずログのみになる）。

## 2. WSL2 のインストール

PowerShellを**管理者として**開いて実行:

```powershell
wsl --install -d Ubuntu
```

インストール後にPCを再起動し、初回起動時にUbuntuのユーザー名・パスワードを設定する。
既にWSLが入っている場合はバージョン確認:

```powershell
wsl -l -v
```

`VERSION`が2になっていることを確認（1の場合は `wsl --set-version Ubuntu 2`）。

## 3. Ubuntu(WSL2)側の準備

WSLのUbuntuターミナルを開いて:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip ffmpeg git
```

- `ffmpeg`: 動画結合・変換に必須（`shutil.which("ffmpeg")`で存在確認される）。
- `python3-venv`: 仮想環境用。

## 4. リポジトリの取得と依存パッケージ

```bash
cd ~
git clone https://github.com/mmtigers/my_python_project.git
cd my_python_project/DDD

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 5. 外付けHDDへの保存先を用意

Windows側でHDDが`E:`ドライブとして見えている前提。WSL側から:

```bash
ls /mnt/e/          # HDDの中身が見えることを確認
mkdir -p /mnt/e/DDD
```

## 6. 環境変数の設定

`~/.bashrc`の末尾（もしくは後述のタスクスケジューラから呼ぶラッパースクリプト）に追記する。
ドライブレターや値は実際の環境に合わせて変更すること。

```bash
# --- batch_download_discord.py 用設定 ---
export VIDEO_SAVE_DIR="/mnt/e/DDD"              # 動画の最終保存先(外付けHDD)
export DDD_REQUIRE_NAS_MOUNT=false              # NAS無し環境なのでマウント確認を無効化
export DDD_LOCAL_TMP_DIR="/mnt/e/DDD/tmp_fragments"  # HLS一時ファイルもHDD側に置く
export ENABLE_YOUTUBE_DL=true                   # YouTubeダウンロードを使うなら

# Discord通知を使う場合のみ(任意。両方省略すると通知なしでログのみになる)
export DISCORD_WEBHOOK_NOTIFY="https://discord.com/api/webhooks/xxxx/yyyy"
export DISCORD_WEBHOOK_ERROR="https://discord.com/api/webhooks/xxxx/zzzz"

# YouTubeのボット検知回避用Cookieを使う場合のみ(任意)
# export YOUTUBE_COOKIES_FILE="/home/<user>/my_python_project/DDD/cookies.txt"
```

> **`DDD_LOCAL_TMP_DIR`について**: 指定しない場合の既定値はスクリプト設置ディレクトリ
> （WSLのLinux側ディスク＝Cドライブ相当の仮想ディスク）配下の`tmp_fragments`になる。
> missav等のHLSダウンロードは1本あたり数GBの一時ファイルを作るため、既定のままだと
> WSLの仮想ディスク容量を圧迫する。上記のように外付けHDD側のパスを明示しておくこと。

設定後、反映:

```bash
source ~/.bashrc
```

## 7. ダウンロード対象リストの用意

`DDD/list.txt`、または`DDD/list/`ディレクトリ配下に`*.txt`ファイルとしてURLを1行ずつ書く
（ファイル名ごとにHDD上のサブフォルダへ振り分けられる）。自宅ラズパイ側で運用しているものが
あれば、その内容をそのままコピーして構わない。

```bash
mkdir -p list
echo "https://www.youtube.com/watch?v=XXXXXXXXXXX" >> list/sample.txt
```

## 8. 動作確認

時間帯制限(既定02:00〜06:00)を無視して即実行できる`--force`で試す:

```bash
cd ~/my_python_project/DDD
source .venv/bin/activate
python3 batch_download_discord.py --force
```

- `⛔ CRITICAL: NASマウントエラー`が出ないこと（出る場合は`DDD_REQUIRE_NAS_MOUNT=false`が
  効いていない＝環境変数の設定漏れ）。
- `/mnt/e/DDD`配下に動画が保存されること。
- Discord Webhookを設定していれば通知が届くこと。

## 9. 定期実行（Windowsタスクスケジューラ + WSL）

WSL2はデフォルトでは常時起動しているわけではないが、`wsl.exe`をタスクスケジューラから
呼び出すと自動的に起動する。既存の自宅ラズパイ運用と同様、決められた時間帯
（既定02:00〜06:00）に定期実行してキューを少しずつ消化する方式にする。

1. WSL側で、環境変数込みで実行するラッパースクリプトを作る:

   ```bash
   cat > ~/run_ddd.sh <<'EOF'
   #!/bin/bash
   source ~/.bashrc
   cd ~/my_python_project/DDD
   source .venv/bin/activate
   python3 batch_download_discord.py >> ~/ddd_run.log 2>&1
   EOF
   chmod +x ~/run_ddd.sh
   ```

2. Windows側でタスクスケジューラ(`taskschd.msc`)を開き、新規タスクを作成:
   - **全般**: 「ユーザーがログオンしているかどうかにかかわらず実行する」にチェック。
   - **トリガー**: 毎日 02:05 開始、「タスクの繰り返し間隔」を30分、
     「継続時間」を3時間55分（≒06:00まで）に設定。「タスクを実行するためにスリープを解除する」
     もチェック。
   - **操作**: プログラム/スクリプトに `wsl.exe`、引数に以下を指定。

     ```
     -d Ubuntu -u <あなたのUbuntuユーザー名> -- /home/<あなたのUbuntuユーザー名>/run_ddd.sh
     ```

   - **条件**: 「AC電源接続時のみ実行する」のチェックを外す（デスクトップPCなら無関係）。

3. PCの電源設定で、深夜にスリープ/休止しないようにする（「電源とスリープ」設定でスリープを
   「なし」にするか、上記の「スリープを解除して実行」に加えてタイマーによるスリープ復帰を
   許可する設定にする）。

これで、スクリプト自身が持つ実行許可時間帯チェック・1回あたりのタスク数上限
（`MAX_TASKS_PER_RUN`）・ボット検知時のクールダウンといった安全機構はそのまま活きつつ、
夜間に少しずつダウンロードが進む（自宅ラズパイでのcron運用と同じ考え方）。

## 10. 運用上の注意点

- **外付けHDDは実行中は取り外さない。** 書き込み中に取り外すとデータ破損の恐れがある。
- **ディスク空き容量**: `MIN_FREE_SPACE_GB`(既定50GB)を下回ると自動的に停止する。HDDの空き
  容量には注意する。
- **NAS本体との関係**: 今回の変更は「単身赴任先PCで完結させる」ためのものであり、自宅の
  ラズパイ＋NAS運用（`DDD_REQUIRE_NAS_MOUNT`は未設定＝従来通り`true`）には一切影響しない。
  同じスクリプト・同じリポジトリを、環境変数だけで両方の環境に対応させている。
- 単身赴任先PCと自宅NASの間で映像を将来的に同期したくなった場合は、別途バックアップ／同期の
  仕組み（`rsync`等）を検討する（本対応のスコープ外）。

## トラブルシューティング

| 症状 | 確認点 |
| --- | --- |
| `⛔ CRITICAL: NASマウントエラー`で止まる | `DDD_REQUIRE_NAS_MOUNT=false`が`source ~/.bashrc`後の同じシェルで有効か。タスクスケジューラ経由の場合はラッパースクリプト内で`source ~/.bashrc`しているか。 |
| `ffmpeg not found` 的な警告 | `sudo apt install ffmpeg`を実行したか、`which ffmpeg`で確認。 |
| `/mnt/e`が無い/空 | Windows側でそのドライブレターが実際に割り当てられているか`explorer.exe`等で確認。 |
| タスクスケジューラで動かない | まず手動で`wsl.exe -d Ubuntu -u <user> -- /home/<user>/run_ddd.sh`をコマンドプロンプトから実行し、同じ結果になるか切り分ける。 |
| Discord通知が来ない | `DISCORD_WEBHOOK_NOTIFY`/`DISCORD_WEBHOOK_ERROR`のURLが正しいか、Webhookがそのチャンネルで有効か。 |
