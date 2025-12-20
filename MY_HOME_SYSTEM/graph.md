graph TD
    %% 定義: ユーザーとインターフェース
    User[👪 家族 (LINE/ブラウザ)]
    LinePF[LINE Platform]
    Discord[Discord (ログ/エラー)]
    
    %% 定義: 外部クラウドサービス & デバイス
    subgraph External_Cloud [☁️ 外部クラウド・API]
        Gmail[Gmail (給与/買物/美容院)]
        SB_Cloud[SwitchBot API]
        NR_Cloud[Nature Remo API]
        PublicInfo[天気/ニュース/運行情報/アプリ]
    end

    subgraph Local_Network [🏠 ローカルネットワーク]
        Cam[📷 ONVIFカメラ]
        SB_Dev[SwitchBotデバイス]
        NR_Dev[Nature Remoデバイス]
    end

    %% 定義: Raspberry Pi 内部
    subgraph Raspberry_Pi [🍓 Raspberry Pi (MY_HOME_SYSTEM)]
        direction TB
        
        %% インフラ層
        Ngrok[ngrok (外部公開)]
        DB[(sqlite3: home_system.db)]
        Assets[📂 Assets (画像/ログ)]

        %% コアサーバー層
        Server[🚀 unified_server.py<br>(FastAPI)]
        
        %% 監視・収集層 (定期実行/常駐)
        subgraph Collectors [監視・収集エージェント]
            CamMon[camera_monitor.py]
            SBMon[switchbot_power_monitor.py]
            NRMon[nature_remo_monitor.py]
            MailMon[shopping/salary/haircut_monitor]
            InfoServ[weather/news/train/app_ranking]
        end

        %% 可視化層
        Dash[📊 dashboard.py<br>(Streamlit)]
        
        %% 自動化・保守
        Cron[⏰ cron_reporter.py]
        Watch[🐕 server_watchdog.py]
    end

    %% --- 接続関係 ---

    %% ユーザーインタラクション
    User <--> LinePF
    User -->|閲覧| Ngrok
    Ngrok -->|HTTP| Dash

    %% LINE Webhookフロー
    LinePF -->|Webhook| Ngrok
    Ngrok -->|localhost:8000| Server
    Server -->|Reply/Push| LinePF

    %% 通知フロー
    Server -->|通知| Discord
    Collectors -->|検知時通知| LinePF & Discord
    Cron -->|定期レポート| LinePF & Discord
    Watch -->|死活監視| Discord

    %% データ収集フロー
    SB_Dev -.-> SB_Cloud
    NR_Dev -.-> NR_Cloud
    
    SBMon -->|Polling| SB_Cloud
    NRMon -->|Polling| NR_Cloud
    MailMon -->|IMAP| Gmail
    InfoServ -->|API/RSS| PublicInfo
    CamMon -->|RTSP/ONVIF| Cam

    %% データ保存フロー
    Server -->|イベント記録| DB
    Collectors -->|データ保存| DB
    CamMon -->|画像保存| Assets
    MailMon -->|画像保存| Assets

    %% データ参照フロー
    Dash -->|可視化| DB & Assets
    Server -->|コンテキスト取得| DB