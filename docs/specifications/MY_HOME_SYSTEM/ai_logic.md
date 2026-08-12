# ai_logic.py（廃止）

`handlers/ai_logic.py` は削除された。

## 廃止理由

このファイルが定義していた `declare_child_health` / `declare_shopping` / `declare_defecation`
（Gemini Function Calling用の宣言スタブ、中身は `pass` のみ）と、それに対応する
`execute_child_health` 等の実行ロジックは、互いに配線されていない未接続のペアだった。

さらに調査の結果、このファイルを呼び出していた唯一の経路 `handlers/line_logic.py` の
`handle_message()` 自体が、本番のLINE Webhook経路（`handlers/line_handler.py`）から
一切呼び出されておらず到達不能なコードであることが判明した。実際のテキストメッセージ処理は
`handlers/line_handler.py` の `_process_message_async()` が担っており、AI解析は
`services/ai_service.py`（`declare_*`/`execute_*` に相当する `tools_schema` と
`tool_record_child_health` 等が正しく配線・テスト済み）に一本化されている。

そのため `handlers/ai_logic.py` と、それを呼んでいた `handlers/line_logic.py` の
`handle_message()` / `ask_outing_question()` / `handle_child_record()` /
`handle_stomach_record()`、および関連する `USER_INPUT_STATE` ステートマシン
（`models/line.py` の `InputMode` / `UserInputState` 含む）を、実害のないデッドコードとして
まとめて削除した。

現在AI解析の実装・仕様は `services/ai_service.py` を参照。
