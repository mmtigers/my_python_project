# ファミクエ Alexaスキル セットアップ手順

「アレクサ、ファミクエを開いて」でEcho Showに家族クエストのメイン画面(家族ごとの
レベル・EXP・ゴールド・承認待ち件数)を表示するための、Alexaカスタムスキル設定手順。

スキルのバックエンドはAWS Lambdaを使わず、既存の `MY_HOME_SYSTEM/unified_server.py`
(LINE Webhookなどと同じFastAPIサーバー)に新しいエンドポイント `/webhook/alexa` を
追加する形で実装済み。すでにLINE Bot用に公開HTTPSエンドポイントを運用しているはず
なので、そのドメインに新しいパスを1つ足すだけで動く。

コード側の実装:

- `MY_HOME_SYSTEM/core/alexa_verifier.py` — Alexaからのリクエストの署名検証
- `MY_HOME_SYSTEM/handlers/alexa_handler.py` — LaunchRequest等のハンドラ、APL画面の組み立て
- `MY_HOME_SYSTEM/alexa/apl/main_screen.json` — Echo Show等に表示するAPL(画面)定義
- `MY_HOME_SYSTEM/routers/alexa_router.py` — `/webhook/alexa` エンドポイント

このディレクトリ (`alexa-skill/`) の中身はコードではなく、Amazon Developer Console
に登録するスキル設定(呼び出し名・インタラクションモデル等)の参考ファイル。
Developer Consoleへの登録はこのセッションからは実行できないため、手動で設定する。

## 前提条件

- Amazon開発者アカウント(https://developer.amazon.com/ )
- unified_serverが公開HTTPSで到達可能であること(LINE Webhookが動いているなら満たしている)
- 証明書は正当なCA発行のものであること(自己署名不可。Let's EncryptなどでOK)

## 手順

### 1. コード側の準備

```bash
cd MY_HOME_SYSTEM
pip install -r requirements.txt   # ask-sdk-core / ask-sdk-model が追加されています
```

`.env` に以下を追加(スキルIDはスキル作成後に判明するので、いったん未設定のままでも動く。
未設定の場合は起動時に警告ログが出るだけで、署名検証自体は有効なまま動作する):

```
ALEXA_SKILL_ID=amzn1.ask.skill.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

unified_serverを再起動すると `/webhook/alexa` (POST) が有効になる。

### 2. Alexa Developer Consoleでスキルを作成

1. https://developer.amazon.com/alexa/console/ask にログイン
2. 「スキルを作成」
   - スキル名: `ファミクエ`
   - デフォルトの言語: 日本語 (JP)
   - 使用するモデル: **カスタム**
   - ホスティングサービス: **自分でプロビジョニングする**(Lambdaは使わない)
3. スキルが作成されたら、上部に表示される **スキルID** (`amzn1.ask.skill.xxxx`) を
   `.env` の `ALEXA_SKILL_ID` に設定し、unified_serverを再起動する

### 3. 呼び出し名とインタラクションモデルを設定

1. 左メニュー「呼び出し名」→ スキルの呼び出し名を `ファミクエ` に設定して保存
2. 左メニュー「JSON エディタ」を開き、`skill-package/interactionModels/custom/ja-JP.json`
   の中身をそのまま貼り付けて保存
3. 「モデルを保存」→「モデルをビルド」

### 4. エンドポイントを設定

1. 左メニュー「エンドポイント」
2. **HTTPS** を選択
3. デフォルトの地域に `https://<あなたの公開ドメイン>/webhook/alexa` を入力
   (`skill-package/skill.json` の `apis.custom.endpoint.uri` も参考用に同じ値に
   書き換えておくと良い)
4. SSL証明書の種類は、実際の証明書に合わせて選択
   (Let's Encrypt等の一般的なCA発行証明書なら「私のエンドポイントには、認証局が
   発行したワイルドカード証明書があります」または該当する選択肢)
5. 保存

### 5. APL(画面表示)を有効化

1. 左メニュー「インターフェース」
2. **Alexa Presentation Language (APL)** をONにする
3. 保存 →「モデルをビルド」

### 6. 動作確認

- Developer Consoleの「テスト」タブで、スキルのテストを「開発中」に切り替える
- テキスト入力欄に `ファミクエを開いて` と入力し、右側にAPL画面(家族カード)の
  プレビューが表示されれば成功
- 同じAmazonアカウントに紐づくEcho Showでも、この時点で
  「アレクサ、ファミクエを開いて」と話しかければ動作する
  (公開審査(認定)は不要。開発者アカウント本人のデバイスでは
  テスト有効化だけで動く)

### 7. 家族の他のAmazonアカウントでも使いたい場合

Developer Consoleの「配布」タブ →「ベータテスト」から、家族のAmazonアカウントの
メールアドレスを招待する。招待を承諾すると、そのアカウントのEcho Showでも
「アレクサ、ファミクエを開いて」が使えるようになる。Alexa Skills Storeへの公開・
Amazonによる認定審査は不要(このスキルは家族内利用のみを想定)。

## トラブルシューティング

- `/webhook/alexa` が400を返す
  - 署名(`Signature`ヘッダ)またはリクエストタイムスタンプの検証失敗。
    `unified_server`のログに `Alexa request signature verification failed` /
    `Alexa request timestamp verification failed` が出ていないか確認する
  - サーバーの時刻がずれているとタイムスタンプ検証(前後150秒)に失敗するので、
    Raspberry Pi側のNTP同期を確認する
  - エンドポイントURLの証明書がAmazonの要件(正当なCA発行、自己署名不可)を
    満たしているか確認する
- APL画面が表示されず音声だけになる
  - Echo Show側ではなく、画面のないAlexaデバイス(Echo Dot等)でテストしていないか確認する
  - Developer Console「インターフェース」でAPLがONになっているか確認する
