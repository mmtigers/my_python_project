# 実機の `.venv` を Python 3.11 で作り直す

Issue #1033（実機の Python が 3.13 で、`MY_HOME_SYSTEM/.python-version` の 3.11 と食い違っていた）の
対処を手順にしたもの。`health_watch` の `python_version` チェック（#1049）が異常を出したときに使う。

## 前提

- 実機は Raspberry Pi OS（Debian 13 trixie）。システムの Python は 3.13 で、**apt に python3.11 は無い**。
  そのため `uv` で 3.11 を入れる（`~/.local/bin` に入り、システムの Python には触らない）。
- `MY_HOME_SYSTEM/.venv` は **MY_HOME_SYSTEM と DDD の共有**（`run_task.sh` は DDD のバッチも
  この venv で動かす。`DDD/.venv` は無い）。**`requirements.txt` は両方入れること。**
  DDD 側を忘れると yt-dlp が消えて、バッチが無音で失敗する。

## 手順

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.11

cd /home/masahiro/develop/MY_HOME_SYSTEM
sudo systemctl stop home_system.service
mv .venv .venv.py313.bak
uv venv --seed --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt
uv pip install --python .venv/bin/python -r ../DDD/requirements.txt
sudo systemctl start home_system.service
```

- `--seed` を付ける（pip を venv に入れる。`start_all.sh --prepare` の鮮度チェックが pip を使う）。
- 新しい `.venv` には `.requirements-sha256` が無いので、次回の起動で
  `requirements changed: updating .venv` が1回出て pip install が走る。正常。

## 落とし穴

### cron と重ねない

`.venv` を退避している間、cron のジョブ（`newface_monitor` は毎時 :07、`health_watch` は :10 など）が
走ると、移動後の古い venv の証明書パス（certifi の `cacert.pem`）を見て失敗する。
Issue #1033 の作業では 21:07 の `newface_monitor` が全サイト失敗し、`health_watch` の
`app_logs` が 48 件のエラーを数えた。**切替は毎時 :12〜:55 の間に済ませる。**
重なった場合のエラーは次の時間の実行で消える（`app_logs` は直近のログ行で数える）。

### 退避した venv の `pip` で比較しない

venv を `mv` すると `bin/pip` の shebang は元のパス（＝新しい venv）を指したままになる。
旧環境のパッケージ一覧は `.venv.py313.bak/bin/python -m pip freeze` で取ること
（`bin/pip freeze` は新環境の中身を返す）。

## 確認

```bash
.venv/bin/python --version                       # Python 3.11.x
.venv/bin/python -c "import yt_dlp; print(yt_dlp.version.__version__)"
systemctl is-active home_system.service
tail -5 logs/health_watch.log                    # 次の :10 以降に python_version / app_logs が出ない
```

`.venv.py313.bak` は数日問題が出なければ削除してよい。
