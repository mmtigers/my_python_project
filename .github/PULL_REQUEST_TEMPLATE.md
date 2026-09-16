## 概要

<!-- 何を、なぜ変更したか。対応する Issue があれば `Closes #NNN` -->

## 変更内容

<!-- 主な変更点を箇条書きで -->

## 確認

<!-- 実行したものにチェック。該当しない項目は消してよい -->

- [ ] `MY_HOME_SYSTEM`: `python -m pytest tests/`(env: `SQLITE_DB_PATH=:memory: NAS_MOUNT_POINT=./tmp_nas NOTIFICATION_TARGET=none`)
- [ ] `MY_HOME_SYSTEM`: `ruff check . --select F821,F822,F823,E9` / `npx pyright` / `bandit -r . -x ./tests -lll`
- [ ] `DDD`: `python -m pytest`(`core.*` のシグネチャを変えた場合は必須)
- [ ] `family-quest`: `npm run lint && npm run build && npm test`
- [ ] シェルスクリプトを変えた場合: `shellcheck -x`
- [ ] 依存を変えた場合: `requirements.in` / `requirements-dev.in` を編集して `pip-compile` で lock を再生成した
- [ ] マイグレーションを追加・変更した場合: `python init_unified_db.py --dump-schema` で `current_schema.sql` を再生成した
- [ ] `config.py` に環境変数を追加した場合: `.env.example` にプレースホルダーを追記した
- [ ] `docs/specifications/` の対応する仕様書を更新した(`python3 .github/scripts/check_spec_line_refs.py` が通る)
- [ ] 実機作業が必要な場合(systemd / crontab / `.env`): 手順を本文に書いた
