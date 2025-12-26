import streamlit as st
import shioaji as sj
import threading
import time
from datetime import datetime
import os
from dotenv import load_dotenv
import pandas as pd

# 匯入模組
from modules.utils import log
from modules.api_service import get_positions_df, get_historical_highs
from modules.logic import monitor_logic
from modules.chart_utils import draw_stock_chart

# Load environment variables
load_dotenv()

# ==========================================
# 初始化與設定
# ==========================================

st.set_page_config(
    page_title="永豐金庫存智慧監控機器人",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 初始化 Session State
if 'api' not in st.session_state:
    st.session_state.api = None
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'monitoring' not in st.session_state:
    st.session_state.monitoring = False
if 'log_messages' not in st.session_state:
    st.session_state.log_messages = []
if 'monitor_thread' not in st.session_state:
    st.session_state.monitor_thread = None
if 'max_prices' not in st.session_state:
    st.session_state.max_prices = {}
if 'positions_df' not in st.session_state:
    st.session_state.positions_df = pd.DataFrame()
if 'latest_prices' not in st.session_state:
    st.session_state.latest_prices = {}
if 'stop_monitor_event' not in st.session_state:
    st.session_state.stop_monitor_event = None

# ==========================================
# UI 介面
# ==========================================

# --- Sidebar: 帳號與控制 ---
st.sidebar.title("🔐 帳號與憑證")

# Helper function to get config safely
def get_config(key, default=""):
    # Try getting from Streamlit secrets first (for Cloud)
    try:
        if key in st.secrets:
            return st.secrets[key]
    except FileNotFoundError:
        pass # secrets.toml not found
    except Exception:
        pass
    # Fallback to os.getenv (for Local .env)
    return os.getenv(key, default)

api_key = st.sidebar.text_input("API Key", value=get_config("SHIOAJI_API_KEY"), type="password")
secret_key = st.sidebar.text_input("Secret Key", value=get_config("SHIOAJI_SECRET_KEY"), type="password")
person_id = st.sidebar.text_input("Person ID (身分證)", value=get_config("SHIOAJI_CERT_PERSON_ID"))

# PFX File Handling
use_uploaded_pfx = st.sidebar.toggle("使用上傳憑證 (Cloud)", value=True)
pfx_path = ""
pfx_pass = st.sidebar.text_input("憑證密碼", value=get_config("SHIOAJI_CERT_PASSWORD"), type="password")

if use_uploaded_pfx:
    uploaded_pfx = st.sidebar.file_uploader("上傳 .pfx 憑證", type=["pfx"])
    if uploaded_pfx:
        # Save to a temp file
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pfx") as tmp_file:
            tmp_file.write(uploaded_pfx.read())
            pfx_path = tmp_file.name
else:
    pfx_path = st.sidebar.text_input("本機憑證路徑 (.pfx)", value=get_config("SHIOAJI_CERT_PATH", "D:/Sinopac/Sinopac.pfx"))

if st.sidebar.button("登入並取得庫存"):
    if not api_key or not secret_key or not pfx_path or not pfx_pass or not person_id:
        st.sidebar.error("請輸入完整登入資訊")
    else:
        try:
            if st.session_state.api is None:
                st.session_state.api = sj.Shioaji()
            
            # Login
            st.session_state.api.login(
                api_key=api_key,
                secret_key=secret_key
            )
            # Activate CA
            st.session_state.api.activate_ca(
                ca_path=pfx_path,
                ca_passwd=pfx_pass,
                person_id=person_id
            )
            
            st.session_state.logged_in = True
            st.sidebar.success("登入成功！憑證已啟用")
            log("系統登入成功")
            
        except Exception as e:
            st.sidebar.error(f"登入失敗: {e}")
            log(f"登入失敗: {e}")


# ==========================================

# --- Main: 主畫面 ---
st.title("🤖 庫存智慧監控機器人")

# Status Bar
if st.session_state.monitoring:
    st.info("🔥 監控中... (請勿關閉視窗)", icon="✅")
else:
    st.warning("⛔ 目前停止監控", icon="⚠️")

# 策略參數區塊
st.subheader("1. 策略參數設定")
col1, col2, col3 = st.columns(3)

with col1:
    start_date = st.date_input(
        "庫存基準日期 (追溯最高價用)",
        value=datetime(2025, 12, 16),
        help="程式會抓取從此日期至今的「歷史最高價」，作為移動停損的計算基準。"
    )

with col2:
    order_type = st.selectbox(
        "下單模式 (Order Type)",
        options=["ROD", "IOC", "FOK"],
        index=0,
        disabled=st.session_state.monitoring
    )

with col3:
    trailing_stop = st.number_input(
        "移動停損/停利回檔百分比 (%)",
        min_value=0.1, value=15.0, step=0.1, format="%.1f",
        disabled=st.session_state.monitoring
    )

st.markdown("---")

# 庫存列表區塊
st.subheader("2. 庫存清單")

if st.session_state.logged_in and st.session_state.api:
    # 重新整理按鈕 logic
    if st.button("🔄 如果沒看到庫存，請點此重新整理庫存") or st.session_state.positions_df.empty:
        new_df = get_positions_df(st.session_state.api)
        if not st.session_state.positions_df.empty and not new_df.empty:
            old_map = st.session_state.positions_df.set_index('代碼')['長期投資'].to_dict()
            new_df['長期投資'] = new_df['代碼'].map(old_map).fillna(False)
        st.session_state.positions_df = new_df

    if not st.session_state.positions_df.empty:
        # 更新歷史最高價
        if '區間最高價' not in st.session_state.positions_df.columns:
             st.session_state.positions_df['區間最高價'] = 0.0
             
        need_fetch_codes = []
        for idx, row in st.session_state.positions_df.iterrows():
             if row['區間最高價'] == 0:
                 need_fetch_codes.append(row['代碼'])
        
        if need_fetch_codes:
            start_date_str = start_date.strftime("%Y-%m-%d")
            highs_map = get_historical_highs(st.session_state.api, need_fetch_codes, start_date_str)
            for idx, row in st.session_state.positions_df.iterrows():
                code = row['代碼']
                if code in highs_map:
                    st.session_state.positions_df.at[idx, '區間最高價'] = highs_map[code]

        # 計算預估價格
        for idx, row in st.session_state.positions_df.iterrows():
            base_high = row['區間最高價']
            if base_high == 0:
                base_high = row['現價'] if row['現價'] > 0 else row['成本']
            
            current_price = row['現價']
            if current_price > base_high:
                base_high = current_price
            
            if row['長期投資']:
                 st.session_state.positions_df.at[idx, '預估出場價'] = 0
                 st.session_state.positions_df.at[idx, '監控狀態'] = "不監控"
            else:
                 st.session_state.positions_df.at[idx, '預估出場價'] = base_high * (1 - trailing_stop / 100)
                 if st.session_state.monitoring:
                     st.session_state.positions_df.at[idx, '監控狀態'] = "🔥 監控中"
                 else:
                     st.session_state.positions_df.at[idx, '監控狀態'] = "未監控"

        # 使用最新的即時價格更新 DataFrame (如果有)
        if 'latest_prices' in st.session_state:
            for idx, row in st.session_state.positions_df.iterrows():
                code = row['代碼']
                if code in st.session_state.latest_prices:
                    # 更新現價
                    st.session_state.positions_df.at[idx, '現價'] = st.session_state.latest_prices[code]
                    # 重新計算預估出場價 (因為現價變了，如果現價創高，預估出場價也要變)
                    # 注意：這裡的邏輯需要跟 monitor_logic 保持一致，或者是純粹顯示
                    # monitor_logic 裡已經有 trailing stop 邏輯。
                    # 這裡為了顯示正確，我們重算一次簡單的 (或者直接拿 monitor_logic 的結果? 但 logic 沒存結果)
                    # 簡單重算：
                    base_high = row['區間最高價']
                    current_p = st.session_state.latest_prices[code]
                    if current_p > base_high:
                         st.session_state.positions_df.at[idx, '區間最高價'] = current_p
                         base_high = current_p
                    
                    if not row['長期投資']:
                        st.session_state.positions_df.at[idx, '預估出場價'] = base_high * (1 - trailing_stop / 100)

        edited_df = st.data_editor(
            st.session_state.positions_df,
            use_container_width=True,
            column_config={
                "長期投資": st.column_config.CheckboxColumn("長期投資 (不監控)", default=False),
                "區間最高價": st.column_config.NumberColumn("區間最高價", format="%.2f"),
                "預估出場價": st.column_config.NumberColumn("預估出場價", format="%.2f"),
                "成本": st.column_config.NumberColumn("成本", format="%.2f"),
                "現價": st.column_config.NumberColumn("現價", format="%.2f"),
            },
            disabled=["代碼", "名稱", "股數", "成本", "現價", "監控狀態", "預估出場價", "區間最高價"],
            hide_index=True,
            key="inventory_editor"
        )
        st.session_state.positions_df = edited_df
    else:
        st.info("目前無庫存")
else:
    st.info("請先於左側登入以查看庫存")

st.markdown("---")

# 即時日誌區
st.subheader("📝 即時監控日誌")
log_container = st.empty()
text_logs = "\n".join(st.session_state.log_messages)
log_container.text_area("System Logs", value=text_logs, height=300, disabled=True)
if st.session_state.monitoring:
   st.caption("ℹ️ 監控執行中。請手動整理或操作介面查看最新狀態。")

# K線圖檢視區塊
if st.session_state.logged_in and not st.session_state.positions_df.empty:
    st.markdown("---")
    st.subheader("📈 個股走勢 (K線 + 20MA + 60MA)")
    
    for idx, row in st.session_state.positions_df.iterrows():
        code = row['代碼']
        name = row['名稱']
        st.markdown(f"**{code} {name}**")
        draw_stock_chart(st.session_state.api, code, days=100)
        st.markdown("---")

# ==========================================
# 處理 Sidebar 按鈕邏輯 (延後處理以確保取得最新 Input 值)
# ==========================================
# 由於 Streamlit 按鈕在 Sidebar 定義時就已經回傳 bool，我們無法"延後"讀取。
# 但我們可以重新檢查 session_state 中的值。
# 不過這裡有一個 trick: 如果我們在 sidebar 定義按鈕時，這些 input widget 還沒被定義
# 那我們就讀不到變數 `trailing_stop`。
# 但 Streamlit 的 script 是從頭跑到尾。
# 所以我們必須把 Sidebar 的按鈕邏輯移到最後面？
# 不，Sidebar 的 render 可以在前面，但邏輯執行必須等待參數。
# 但 button 回傳 True 只有在那一行。
# 妥協解法：使用 st.session_state 做參數傳遞，或是接受參數是"上一次 run 的值" (在 Streamlit 通常沒差，因為 user 改參數會 trigger rerun)。
# 最穩健解法：確認 `start_date`, `order_type`, `trailing_stop` 都有值。
# 因為我們給了 default value，所以它們一定有值。

# 實際上，當 User 點擊 Sidebar 按鈕，Script 重跑，跑到 st.sidebar.button 回傳 True。
# 此時下面的 Inputs (start_date 等) 雖然還沒執行到，但它們會從 Widget State 拿出 User 設定的值。
# 所以只要這些 Widget 有 Key 或者我們信任變數賦值順序...
# 等等，如果 Script 還沒執行到 `start_date = ...`，那 `start_date` 變數還不存在。
# 所以我們不能在上方直接用 `start_date` 變數。

# 修正：
# 我們將 Sidebar 的 "按鈕 UI" 保留在上面，但 "按鈕邏輯" 移到下面。
# 但 `if st.sidebar.button(...)` 必須包住邏輯。
# 我們可以用一個 flag。

start_monitoring = False
stop_monitoring = False

# Sidebar 重新定義按鈕區
# 為了避免重複定義 ID，我們使用一個 container
# Sidebar 重新定義按鈕區
# 為了避免重複定義 ID，我們使用一個 container
with st.sidebar:
    # 監控控制區
    col_start, col_stop = st.columns(2)
    with col_start:
        if st.button("🚀 啟動監控", disabled=st.session_state.monitoring or not st.session_state.logged_in, use_container_width=True):
            start_monitoring = True
    
        if st.button("🛑 停止監控", disabled=not st.session_state.monitoring, use_container_width=True):
            stop_monitoring = True
            
    auto_refresh = st.checkbox("監控時自動更新介面 (3秒)", value=True, disabled=not st.session_state.monitoring)

    st.markdown("---")
    # 登出區
    if st.session_state.logged_in:
        if st.button("👋 登出系統", type="secondary", use_container_width=True):
            try:
                if st.session_state.api:
                    st.session_state.api.logout()
            except Exception as e:
                pass # Ignore logout errors
            
            # 清除狀態
            st.session_state.logged_in = False
            st.session_state.api = None
            st.session_state.monitoring = False
            stop_monitoring = True
            
    auto_refresh = st.checkbox("監控時自動更新介面", value=True, disabled=not st.session_state.monitoring)
    refresh_seconds = st.slider("刷新間隔 (秒)", min_value=1, max_value=60, value=3, disabled=not auto_refresh)

    st.markdown("---")
    # 登出區
    if st.session_state.logged_in:
        if st.button("👋 登出系統", type="secondary", use_container_width=True):
            try:
                if st.session_state.api:
                    st.session_state.api.logout()
            except Exception as e:
                pass # Ignore logout errors
            
            # 清除狀態
            st.session_state.logged_in = False
            st.session_state.api = None
            st.session_state.monitoring = False
            # Signal stop
            if st.session_state.stop_monitor_event:
                st.session_state.stop_monitor_event.set()
                
            st.session_state.positions_df = pd.DataFrame()
            st.session_state.log_messages = []
            st.success("已登出")
            st.rerun()

# 處理啟動邏輯 (在參數定義之後)
if start_monitoring:
    monitoring_df = st.session_state.positions_df[~st.session_state.positions_df['長期投資']]
    targets = {}
    for _, row in monitoring_df.iterrows():
        targets[row['代碼']] = {'cost': row['成本'], 'qty': row['股數']}
    
    if not targets:
        st.sidebar.warning("沒有可監控的標的")
    else:
        st.session_state.monitoring = True
        # Reset event
        st.session_state.stop_monitor_event = threading.Event()
        
        thread = threading.Thread(
            target=monitor_logic,
            args=(
                st.session_state.api,
                st.session_state.log_messages,
                st.session_state.latest_prices,
                st.session_state.max_prices,
                st.session_state.stop_monitor_event,
                trailing_stop, order_type,
                targets, 
                start_date.strftime("%Y-%m-%d")
            ),
            daemon=True
        )
        # Adding script run context for thread if needed, but simple thread usually works if not accessing st context heavily.
        # monitor_logic accesses st.session_state. It might work if session is global.
        # Ideally we pass add_report_ctx(thread)
        try:
            from streamlit.runtime.scriptrunner import add_script_run_ctx
            add_script_run_ctx(thread)
        except ImportError:
            pass # Old streamlit version or different structure

        st.session_state.monitor_thread = thread
        thread.start()
        st.rerun()

# 處理停止邏輯
if stop_monitoring:
    st.session_state.monitoring = False
    if st.session_state.stop_monitor_event:
        st.session_state.stop_monitor_event.set()
    log("...正在停止監控...")
    st.rerun()

# 監控中自動刷新
if st.session_state.monitoring and 'auto_refresh' in locals() and auto_refresh:
    time.sleep(refresh_seconds)
    st.rerun()
