# MY_HOME_SYSTEM/train_service.py
import requests
from bs4 import BeautifulSoup
import traceback
import re
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

# 自作モジュール
import common

# ロガー設定
logger = common.setup_logging("train_service")

# JR西日本 運行情報API
JR_WEST_JSON_URL: str = "https://www.train-guide.westjr.co.jp/api/v3/area_kinki_trafficinfo.json"

# Yahoo!路線情報 ベースURL
YAHOO_SEARCH_URL: str = "https://transit.yahoo.co.jp/search/result"

def get_jr_traffic_status() -> Dict[str, Dict[str, Any]]:
    """
    JR西日本の運行状況を取得する
    
    Returns:
        Dict: 路線名をキーとしたステータス情報
    """
    results: Dict[str, Dict[str, Any]] = {
        "宝塚線": {"status": "🟢 平常運転", "detail": "遅れはありません", "is_delay": False, "is_suspended": False},
        "神戸線": {"status": "🟢 平常運転", "detail": "遅れはありません", "is_delay": False, "is_suspended": False}
    }
    
    try:
        resp = requests.get(JR_WEST_JSON_URL, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            # APIレスポンス構造: {"lines": { "G": {...}, "A": {...} }}
            lines = data.get("lines", {})
            
            for line_id, info in lines.items():
                target_name: str = ""
                if line_id == "G": target_name = "宝塚線"
                elif line_id == "A": target_name = "神戸線"
                
                if target_name:
                    status_text: str = info.get("status", "情報あり")
                    detail_text: str = info.get("text", "詳細情報なし")
                    is_suspended: bool = "見合" in status_text or "運休" in status_text
                    
                    results[target_name]["status"] = "🔴 " + status_text
                    results[target_name]["detail"] = detail_text
                    results[target_name]["is_delay"] = True
                    results[target_name]["is_suspended"] = is_suspended
                    
    except Exception as e:
        logger.error(f"JR Traffic API Error: {e}")
        # エラー時はデフォルト(平常運転)を返すことでシステムを止めない
        
    return results

def get_route_info(from_station: str = "伊丹(兵庫県)", to_station: str = "長岡京") -> Dict[str, Any]:
    """
    Yahoo!路線情報から最短ルートを取得
    ※現在時刻の20分後を出発時刻として検索する
    
    Returns:
        Dict: ルート詳細情報
    """
    route_data: Dict[str, Any] = {
        "label": f"{from_station} → {to_station}",
        "departure": "--:--",
        "arrival": "--:--",
        "duration": "--分",
        "transfer": "--回",
        "cost": "----円",
        "details": [],
        "url": "",
        "summary": "取得失敗"
    }
    
    try:
        # 現在時刻 + 20分 を計算
        future_time = datetime.now() + timedelta(minutes=20)
        
        # 検索パラメータ設定
        params = {
            "from": from_station,
            "to": to_station,
            "y": future_time.year,
            "m": future_time.strftime("%m"),
            "d": future_time.strftime("%d"),
            "hh": future_time.hour,
            "m1": future_time.minute // 10,
            "m2": future_time.minute % 10,
            "type": "1", # 1:出発時刻指定
            "s": "0"     # 時間順
        }
        
        resp = requests.get(YAHOO_SEARCH_URL, params=params, timeout=5)
        route_data["url"] = resp.url
        
        if resp.status_code != 200:
            logger.warning(f"Yahoo Route Search failed with status: {resp.status_code}")
            return route_data
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        route_elm = soup.select_one('#rsltlst li.el') or soup.select_one('.routeSummary')
        
        if route_elm:
            # 1. 時間
            time_elm = route_elm.select_one('.time')
            if time_elm:
                time_text = time_elm.get_text(strip=True)
                times = re.findall(r'(\d{1,2}:\d{2})', time_text)
                if len(times) >= 2:
                    route_data["departure"] = times[0]
                    route_data["arrival"] = times[-1]

            # 2. 所要時間
            dur_elm = route_elm.select_one('.time .small') or route_elm.select_one('.small')
            if dur_elm:
                route_data["duration"] = dur_elm.get_text(strip=True).replace("(", "").replace(")", "")

            # 3. 運賃・乗換
            fare_elm = route_elm.select_one('.fare')
            if fare_elm: route_data["cost"] = fare_elm.get_text(strip=True)
            trans_elm = route_elm.select_one('.transfer')
            if trans_elm: route_data["transfer"] = trans_elm.get_text(strip=True)

            # 4. 詳細ルート
            detail_elm = soup.select_one('.routeDetail')
            if detail_elm:
                stations = [s.get_text(strip=True) for s in detail_elm.select('.station dt')]
                lines = [l.get_text(strip=True) for l in detail_elm.select('.transport div')]
                
                details_list: List[str] = []
                if stations: 
                    details_list.append(f"🚉 {stations[0]}")
                
                for i in range(len(lines)):
                    line_name = lines[i].replace("[train]", "").strip()
                    details_list.append(f"⬇️ {line_name}")
                    if i + 1 < len(stations):
                        station_name = stations[i+1]
                        if i + 1 == len(stations) - 1:
                            details_list.append(f"🏁 {station_name}")
                        else:
                            details_list.append(f"🔄 {station_name}")
                route_data["details"] = details_list

            route_data["summary"] = "取得成功"
            
    except Exception as e:
        logger.error(f"Route scrape error: {e}")
        route_data["summary"] = f"エラー: {str(e)}"
        
    return route_data

if __name__ == "__main__":
    # テスト実行用の設定
    # common.setup_logging済みなのでコンソールにも出るはずだが念のため
    print("--- JR Status ---")
    print(get_jr_traffic_status())
    
    print("\n--- Route Info ---")
    print(get_route_info())