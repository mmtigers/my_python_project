# 閉鎖済みサイトの整理手順（Issue #1031）

`newface_monitor` が毎時巡回し続けている閉鎖疑いの3サイト（`restpia` / `mrs_luxe` / `yurikago`）を、
目視確認のうえ `DDD/sites.json` から整理するための手順書。**確認と削除の実行は人間が行う**
（実サイトの目視が要るため、自動化していない）。

## 前提（調査で分かったこと）

- `DDD/sites.json` は **git 管理下のファイル**（gitignore 対象ではない）。Issue 起票時の「実機・gitignore対象の場合は実機で」は当てはまらず、削除は通常の PR で行い、実機へは `git pull` で反映できる。
- 3サイトとも閉鎖アラート送信済みで、ログは WARNING に降格済み（#1029 以降は転送系も閾値前から WARNING）。ただし毎時の巡回は続いており、ログと時間を消費している。
- `DDD/test_newface_monitor_sites_json.py` の `test_site_count_is_79` が **サイト数を79に固定**している。サイトを削除したら、この件数も同じ PR で更新すること（更新しないと CI が落ちる）。

## 3サイトの状況（Issue 起票時点: 10/24 15:00）

| site_id | target_url | 連続失敗 | 症状 | 判定 |
| --- | --- | --- | --- | --- |
| `restpia` | `https://restpia-es.com/therapist.html` | 239回 | `div.staff-box:has(a)` が0件 | **閉鎖かセレクタ破損か未判別**（先に目視確認） |
| `mrs_luxe` | `https://luxe-mrs.com/lady.php` | 558回 | `luxe-mrs.com` の名前解決失敗（他サイトは解決できる）。1回の実行で約40秒消費 | 閉鎖の疑いが強い |
| `yurikago` | `https://yurikago-kyoto.com/therapist/` | 47回 | `estama.jp` へ転送 | 閉鎖・移転の疑い |

## 手順

### 1. restpia（先に目視確認）

1. ブラウザで `https://restpia-es.com/therapist.html` を開く。
2. 判定:
   - **サイトが無い / 別サイトに転送される / 在籍一覧が出ない** → 閉鎖。手順2へ（削除）。
   - **在籍一覧が表示される** → セレクタ破損。削除せず、ページのHTMLを見て `sites.json` の `selector_container`（現在 `div.staff-box:has(a)`）・`selector_name`・`selector_link`・`selector_image` を直す。直したら手順3の確認を行う。

### 2. mrs_luxe / yurikago（閉鎖の確認）

1. `https://luxe-mrs.com/lady.php` と `https://yurikago-kyoto.com/therapist/` をブラウザで開く。
2. 閉鎖・移転済みであることを確認する。
   - `mrs_luxe`: 開けない（名前解決失敗）なら閉鎖。ブラウザでも開けるなら、ラズパイ側の DNS 問題を疑い、削除せず別途調査する。
   - `yurikago`: `estama.jp` へ転送されるだけで、元の在籍一覧が無ければ閉鎖。移転先に在籍一覧が出ており追跡したい場合は、削除ではなく `target_url` とセレクタの差し替えを検討する。

### 3. sites.json から削除する PR を出す

閉鎖を確認したサイトだけを対象にする。

1. `DDD/sites.json` から該当エントリ（`site_id` が一致するオブジェクト）を削除する。配列の要素なので、前後のカンマに注意する。
2. `DDD/test_newface_monitor_sites_json.py` の `test_site_count_is_79` の件数を、削除後のサイト数に更新する（3件すべて削除すれば76）。
3. 確認:

   ```bash
   cd DDD
   export SQLITE_DB_PATH=":memory:" NAS_MOUNT_POINT="./tmp_nas" NOTIFICATION_TARGET="none"
   python -m pytest test_newface_monitor_sites_json.py -v
   python -m pytest -v --cov=. --cov-fail-under=74
   ```

4. `docs/specifications/DDD/newface_monitor.md` にサイト数や該当サイト名を書いた箇所があれば追従する（`grep -n "79\|restpia\|mrs_luxe\|yurikago"` で探す）。
5. PR を出し、マージする。実機では `git pull` で反映される。

### 4. 反映後の確認（実機）

次の毎時7分（#1035 で毎時0分から変更）の実行後、DDD のログに該当サイトが出なくなったことを確認する。

```bash
grep -c "restpia\|mrs_luxe\|yurikago" /home/masahiro/develop/DDD/logs/newface_monitor.log
# 直近の実行分に新しい行が増えていないこと（過去分は残る）
tail -n 200 /home/masahiro/develop/DDD/logs/newface_monitor.log | grep "restpia\|mrs_luxe\|yurikago"
```

## 後始末（任意）

- 削除したサイトの `known_casts_<site_id>.json` と、`site_failures.json` 内のエントリは残る。巡回は `sites.json` の登録サイトに対して行うので参照されない想定だが、残骸が気になる場合は実機のデータディレクトリから手で消してよい（先にバックアップを取る）。
- 休眠の自動化（長期失敗サイトを自動で休眠させる）は #1032 で検討したが、見送りとした。今回のような整理は本手順で人間が行う。
