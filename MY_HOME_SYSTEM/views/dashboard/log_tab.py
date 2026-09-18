# MY_HOME_SYSTEM/views/dashboard/log_tab.py
import streamlit as st
import pandas as pd
import subprocess

# #651: systemctl 等の外部コマンドが応答しない場合にダッシュボードを固めないための上限(秒)。
SUBPROCESS_TIMEOUT_SEC: int = 30
from datetime import date
from services import analysis_service

def render_logs(df_sensor: pd.DataFrame):
    """センサーログ分析(場所での絞り込み + 一覧)"""
    if df_sensor.empty:
        st.info("センサーログがありません")
        return

    locs = df_sensor["location"].unique()
    sel = st.multiselect("場所", locs, default=locs)
    st.dataframe(
        df_sensor[df_sensor["location"].isin(sel)][
            ["timestamp", "friendly_name", "location", "contact_state", "power_watts"]
        ].head(200),
        width="stretch",
    )


def render_resources():
    """ディスク・メモリの使用率"""
    st.markdown("##### 💻 リソース状況")
    disk = analysis_service.get_disk_usage()
    if disk:
        st.write(f"**💾 ディスク使用率: {disk['percent']:.1f}%**")
        st.progress(int(disk["percent"]))

    st.write("")
    mem = analysis_service.get_memory_usage()
    if mem:
        st.write(f"**🧠 メモリ使用率: {mem['percent']:.1f}%**")
        st.progress(int(mem["percent"]))


def render_nas_status():
    """NASのPing/マウント状態"""
    st.markdown("##### 🗄️ NAS 状態")
    nas_data = analysis_service.load_nas_status()
    if nas_data is None:
        st.info("データなし")
        return

    c1, c2, c3 = st.columns(3)
    with c1: st.metric("Ping疎通", f"{'✅' if nas_data['status_ping']=='OK' else '❌'} {nas_data['status_ping']}")
    with c2: st.metric("マウント", f"{'✅' if nas_data['status_mount']=='OK' else '❌'} {nas_data['status_mount']}")
    with c3: st.metric("最終確認", str(nas_data["timestamp"]))


def render_server_logs():
    """journald由来のサーバーログ検索・表示"""
    st.markdown("##### 📜 サーバーログ")
    search_mode = st.radio("検索モード", ["直近のログを表示", "日付を指定して検索"], horizontal=True)
    # スマホ対応: 以前は st.columns([1, 1, 2]) の3列目を捨てる形で幅を調整していたが、
    # 空列はスマホ幅では縦積みされたぶんだけ無駄な余白になるだけなので、2列にする
    # (幅の調整はCSS側の縦積みルールに委ねる)。
    col_opt1, col_opt2 = st.columns(2)
    target_date = None
    lines_val = 50

    with col_opt1:
        if search_mode == "日付を指定して検索": target_date = st.date_input("対象日", date.today())
        else: lines_val = st.selectbox("表示行数", [50, 100, 200, 500], index=0)

    with col_opt2:
        level_opts = {"全て": None, "警告": "warning", "エラー": "err"}
        sel = st.selectbox("ログレベル", list(level_opts.keys()))
        priority = level_opts[sel]

    if st.button("🔄 ログを更新"): st.rerun()

    logs = analysis_service.get_system_logs(lines=lines_val, priority=priority, target_date=target_date)
    if not logs: st.info("ログなし")
    else: st.code(logs, language="text")


def render_maintenance():
    """サービス再起動・バックアップといった管理操作。

    スマートフォンからも実行できるようにした(オーナー判断)が、誤タップで本番サービスが
    落ちる操作のため、再起動は従来どおり「チェックボックスで理解を確認 → ボタン」の
    2段階を維持する。ボタンのタップターゲットは `common.CUSTOM_CSS` で44px以上を確保している。
    """
    st.markdown("##### 🛠️ メンテナンス操作")
    st.warning("⚠️ この操作は本番サービスを再起動します。誤操作防止のため確認が必要です。")
    confirm_reboot = st.checkbox("再起動することを理解しました", key="confirm_reboot_checkbox")
    if confirm_reboot:
        if st.button("🔄 システム再起動", type="primary"):
            try:
                # #651: timeout が無いと、systemd 側が応答しない状況で Streamlit の
                # スクリプト実行スレッドが無限に待ち、ダッシュボード全体が固まる
                # (再起動対象は自分自身が動くホストのサービスなので、詰まる場面が現実にある)。
                subprocess.run(
                    ["sudo", "systemctl", "restart", "home_system"],
                    check=True,
                    timeout=SUBPROCESS_TIMEOUT_SEC,
                )
                st.success("再起動コマンド送信完了")
            except subprocess.TimeoutExpired:
                st.error(
                    f"再起動コマンドが {SUBPROCESS_TIMEOUT_SEC} 秒以内に完了しませんでした。"
                    "systemctl 側の状態を確認してください。"
                )
            except Exception as e:
                st.error(f"エラー: {e}")

    # バックアップ機能 (簡易実装)
    from services import backup_service
    st.markdown("##### 📦 バックアップ")
    if st.button("今すぐバックアップを実行"):
        success, res, size = backup_service.perform_backup()
        if success: st.success(f"完了: {size:.1f}MB")
        else: st.error(f"失敗: {res}")
