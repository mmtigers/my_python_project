# ai_logic.py (廃止)

## 廃止notice

このファイルが対応していたソース `handlers/ai_logic.py` は削除されました。

- 廃止日: 2026-08
- 廃止理由: このファイルが定義していた `declare_child_health` / `declare_shopping` / `declare_defecation`（Gemini Function Calling用の宣言スタブ、中身は `pass` のみ）と、それに対応する `execute_child_health` 等の実行ロジックは、互いに配線されていない未接続のペアだった。さらに調査の結果、このファイルを呼び出していた唯一の経路 `handlers/line_logic.py` の `handle_message()` 自体が、本番のLINE Webhook経路（`handlers/line_handler.py`）から一切呼び出されておらず到達不能なコードであることが判明した。実際のテキストメッセージ処理は `handlers/line_handler.py` の `_process_message_async()` が担っており、AI解析は `services/ai_service.py`（`declare_*`/`execute_*` に相当する `tools_schema` と `tool_record_child_health` 等が正しく配線・テスト済み）に一本化されている。そのため `handlers/ai_logic.py` と、それを呼んでいた `handlers/line_logic.py` の `handle_message()` / `ask_outing_question()` / `handle_child_record()` / `handle_stomach_record()`、および関連する `USER_INPUT_STATE` ステートマシン（`models/line.py` の `InputMode` / `UserInputState` 含む）を、実害のないデッドコードとしてまとめて削除した。この整理は2026-08のボス/装備/ギルド/マイレージ/週間ランキング機能削除リファクタ(`d1599d6`/`ffdc8c2`/`1818d5a`)とは無関係の、別系統のデッドコード削除である。
- 本仕様書は削除されたソースの記録として残置する。新規の実装・参照の対象にはしないこと。

## 関連ドキュメント

- [ai_service.md](./ai_service.md) — 後継実装。現在のAI解析（Gemini Function Calling）はこちらに一本化されている。
- [line_handler.md](./line_handler.md) — 実際にLINE Webhookのテキストメッセージ処理を担っている呼び出し元（`_process_message_async()`）。
- [line_logic.md](./line_logic.md) — 本ファイルを呼び出していたが、同時に到達不能なデッドコードとして削除された `handlers/line_logic.py` の仕様書。

現在のAI解析の実装・仕様は `services/ai_service.py` を参照。
