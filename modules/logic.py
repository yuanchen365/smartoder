
import pandas as pd
import time
from datetime import datetime

from .api_service import place_sell_order
import yfinance as yf # Fallback source

def monitor_logic(api, log_list, latest_prices, max_prices, stop_event,
                  trailing_stop_pct, order_type_str, targets, start_date_str):
    """
    背景監控邏輯 (執行緒函式)
    args:
        api: Shioaji API instance
        log_list: Shared list for logs (st.session_state.log_messages)
        latest_prices: Shared dict for real-time prices (st.session_state.latest_prices)
        max_prices: Shared dict for max prices (st.session_state.max_prices)
        stop_event: threading.Event to control loop
        ...
    """
    
    def log(message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{timestamp}] {message}"
        log_list.insert(0, full_msg)
        if len(log_list) > 100:
            log_list.pop()

    log("=== 監控服務已啟動 ===")
    
    if not targets:
        log("無監控標的，監控服務停止")
        stop_event.set()
        return

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
            log(f"[{code}] 請求 K 線範圍: {start_date_str} ~ {today_str} (Source: Shioaji)")
            
            historical_high = 0.0
            has_data = False
            
            # --- Tier 1: Shioaji API ---
            try:
                kbars = api.kbars(contract, start=start_date_str, end=today_str, timeout=10000)
                df_k = pd.DataFrame({**kbars})
                if not df_k.empty:
                    historical_high = float(df_k['High'].max())
                    log(f"[{code}] Shioaji 歷史最高價: {historical_high}")
                    has_data = True
                else:
                    log(f"[{code}] Shioaji 查無資料 (Empty)")
            except Exception as e:
                log(f"[{code}] Shioaji API 錯誤: {e}")

            # --- Tier 2: yfinance Fallback ---
            if not has_data:
                try:
                    yf_code = f"{code}.TW"
                    log(f"[{code}] 嘗試使用 yfinance 抓取 ({yf_code})...")
                    yf_df = yf.download(yf_code, start=start_date_str, end=today_str, progress=False)
                    
                    if not yf_df.empty:
                        # yfinance High column might be multi-level if not flattened, but usually simple download is fine or check structure
                        # yfinance 0.2.x returns MultiIndex columns sometimes. Safe max.
                        if 'High' in yf_df.columns:
                            h_col = yf_df['High']
                            historical_high = float(h_col.max()) # Works for Series or single-col DF
                            log(f"[{code}] yfinance 歷史最高價: {historical_high}")
                            has_data = True
                        else:
                            log(f"[{code}] yfinance 資料格式不符 (缺少 High)")
                    else:
                        log(f"[{code}] yfinance 亦無資料")
                except Exception as e:
                    log(f"[{code}] yfinance 失敗: {e}")

            # --- Final Decision ---
            if has_data and historical_high > 0:
                 max_prices[code] = historical_high
            else:
                 log(f"[{code}] ⚠ 查無任何歷史 K 線，將以稍後抓取的現價為基準")
                 
        except Exception as e:
            log(f"[{code}] 抓取歷史資料失敗: {e}")

    log(f"監控標的共 {len(targets)} 檔: {list(targets.keys())}")

    while not stop_event.is_set():
        try:
            # 2. 抓取 Snapshot
            codes = list(targets.keys())
            if not codes:
                log("所有標的已處理完畢，停止監控")
                stop_event.set()
                break
            
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
                # 更新即時價格到 Global State 供 UI 讀取
                latest_prices[code] = current_price

                if code not in max_prices:
                    max_prices[code] = current_price
                    log(f"[{code}] 監控開始，初始價格: {current_price}")
                else:
                    if current_price > max_prices[code]:
                         max_prices[code] = current_price
                
                max_price = max_prices[code]
                
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
                    if code in max_prices:
                        del max_prices[code]
            
            time.sleep(3) 
            
        except Exception as e:
            log(f"監控迴圈發生錯誤: {e}")
            time.sleep(5) 

    log("=== 監控服務已停止 ===")
