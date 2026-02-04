# MY_HOME_SYSTEM/views/dashboard/common.py
import streamlit as st

CUSTOM_CSS = """
<style>
    html, body, [class*="css"] { 
        font-family: "Helvetica Neue", Arial, "Hiragino Kaku Gothic ProN", "Hiragino Sans", Meiryo, sans-serif; 
    }
    .status-card {
        padding: 10px 5px;
        border-radius: 10px;
        text-align: center;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        margin-bottom: 8px;
        height: 90px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
    }
    .status-title {
        font-size: 0.8rem; color: #555; margin-bottom: 5px; font-weight: bold; opacity: 0.8;
    }
    .status-value {
        font-size: 1.1rem; font-weight: bold; line-height: 1.2; white-space: normal; 
    }
    .theme-green { background-color: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9; }
    .theme-yellow { background-color: #fffde7; color: #f9a825; border: 1px solid #fff9c4; }
    .theme-red { background-color: #ffebee; color: #c62828; border: 1px solid #ffcdd2; }
    .theme-blue { background-color: #e3f2fd; color: #1565c0; border: 1px solid #bbdefb; }
    .theme-gray { background-color: #f5f5f5; color: #757575; border: 1px solid #e0e0e0; }
    
    .route-card {
        background-color: #fff; padding: 15px; border-radius: 10px; 
        border: 1px solid #ddd; margin-bottom: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .route-path {
        margin-top: 15px; padding-top: 10px; border-top: 1px dashed #ccc; font-size: 0.95rem; color: #333;
    }
    .station-node { font-weight: bold; color: #000; }
    .line-node { color: #666; font-size: 0.85rem; margin: 0 5px; }
    .transfer-mark { color: #f57f17; font-weight:bold; margin: 0 5px; }
    
    .streamlit-expanderHeader {
        font-weight: bold; color: #0d47a1; background-color: #f0f8ff; border-radius: 5px;
    }
</style>
"""

def render_status_card_html(title: str, value: str, theme: str) -> str:
    """ステータスカードのHTMLを生成"""
    return f"""
    <div class="status-card {theme}">
        <div class="status-title">{title}</div>
        <div class="status-value">{value}</div>
    </div>
    """