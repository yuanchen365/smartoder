import streamlit as st
import pandas as pd
import time
from datetime import datetime
from .utils import log
from .api_service import place_sell_order

def monitor_logic(api_key, secret_key, pfx_path, pfx_pass, 
                  trailing_stop_pct, order_type_str, targets, start_date_str):
    """
    背景監控邏輯 (執行緒函式)
    """
    
    api = st.session_state.api
    log("=== 監控服務已啟動 ===")
    
    if not targets:
        log("無監控標的，監控服務停止")
        st.session_state.monitoring = False
        return

    # 初始化最高價紀錄
    st.session_state.max_prices = {}
    
    # --- 1. 預先抓取歷史最高價 (從指定交易日開始) ---
    log(f"正在抓取歷史資料 (起始日: {start_date_str})...")
    
    for code, info in targets.items():
        try:
            contract = api.Contracts.Stocks.get(code)
            if not contract:
                log(f"[{code}] 找不到合約資訊，將以現價為基準")
                continue
                
            # 抓取 K 線 (日 K)
            today_str = datetime.now().strftime("%Y-%m-%d")
            kbars = api.kbars(contract, start=start_date_str, end=today_str)
            df_k = pd.DataFrame({**kbars})
            
            initial_high = 0.0
            if not df_k.empty:
                historical_high = float(df_k['High'].max())
                initial_high = historical_high
                log(f"[{code}] 歷史區間最高價: {historical_high}")
            else:
                log(f"[{code}] 查無歷史 K 線，將以現價為基準")
            
            if initial_high > 0:
                 st.session_state.max_prices[code] = initial_high
                 
        except Exception as e:
            log(f"[{code}] 抓取歷史資料失敗: {e}")

    log(f"監控標的共 {len(targets)} 檔: {list(targets.keys())}")

    while st.session_state.monitoring:
        try:
            # 2. 抓取 Snapshot
            codes = list(targets.keys())
            if not codes:
                log("所有標的已處理完畢，停止監控")
                st.session_state.monitoring = False
                break
            
            # 修正：api.snapshots 需要傳入 Contract 物件列表，而非單純的代碼字串
            # 錯誤 'str' object has no attribute 'dict' 通常是因為傳入了字串導致內部序列化失敗
            contracts_list = []
            for c in codes:
                contract = api.Contracts.Stocks.get(c)
                if contract:
                    contracts_list.append(contract)
            
            if not contracts_list:
                log("無法取得監控標的之合約資訊，稍後重試...")
                time.sleep(5)
                continue

            snapshots = api.snapshots(contracts_list)
            
            for snap in snapshots:
                code = snap.code
                current_price = snap.close
                
                if current_price == 0: continue 
                
                qty = targets[code]['qty']
                
                # --- A. 更新最高價 ---
                if code not in st.session_state.max_prices:
                    st.session_state.max_prices[code] = current_price
                    log(f"[{code}] 監控開始，初始價格: {current_price}")
                else:
                    if current_price > st.session_state.max_prices[code]:
                         st.session_state.max_prices[code] = current_price
                
                max_price = st.session_state.max_prices[code]
                
                # --- B. 計算邏輯 ---
                exit_price = max_price * (1 - trailing_stop_pct / 100)
                
                triggered = False
                trigger_reason = ""
                
                if current_price <= exit_price:
                    triggered = True
                    trigger_reason = f"觸發移動停損/停利 (現價 {current_price} <= 防守價 {exit_price:.2f}, 波段最高 {max_price})"
                
                # --- C. 觸發下單 ---
                if triggered:
                    place_sell_order(api, code, qty, order_type_str, trigger_reason)
                    # 移除監控
                    del targets[code]
                    if code in st.session_state.max_prices:
                        del st.session_state.max_prices[code]
            
            time.sleep(3) 
            
        except Exception as e:
            log(f"監控迴圈發生錯誤: {e}")
            time.sleep(5) 

    log("=== 監控服務已停止 ===")
