import shioaji as sj
from shioaji import constant
import pandas as pd
from datetime import datetime
import streamlit as st
from .utils import log

def get_positions_df(api):
    """取得庫存並轉換為整潔的 DataFrame"""
    try:
        positions = api.list_positions(unit=constant.Unit.Share)
        
        # 1. 取得所有庫存代碼的 Snapshot 以獲取最新價格 (list_positions 的價格可能是舊的)
        realtime_prices = {}
        valid_positions = [p for p in positions if p.quantity > 0]
        
        if valid_positions:
            contracts = []
            for p in valid_positions:
                contract = api.Contracts.Stocks.get(p.code)
                if contract:
                    contracts.append(contract)
            
            if contracts:
                try:
                    snapshots = api.snapshots(contracts)
                    for snap in snapshots:
                        if snap.close > 0:
                            realtime_prices[snap.code] = snap.close
                except Exception as e:
                    log(f"取得即時報價 Snapshot 失敗: {e}")

        data = []
        for p in valid_positions:
            # 優先使用 Snapshot 的價格，若無則回退到 p.last_price (可能為 0 或昨日收盤)
            real_price = realtime_prices.get(p.code, float(p.last_price) if hasattr(p, 'last_price') else 0.0)
            
            data.append({
                "代碼": p.code,
                "名稱": p.code, # Shioaji Position 物件可能不含名稱，需額外查詢
                "股數": int(p.quantity),
                "成本": float(p.price),
                "現價": real_price,
                "監控狀態": "未監控",
                "長期投資": False # 預設不勾選
            })
        
        if not data:
            return pd.DataFrame(columns=["代碼", "名稱", "股數", "成本", "現價", "監控狀態", "長期投資", "預估出場價", "區間最高價"])
            
        df = pd.DataFrame(data)
        df["預估出場價"] = 0.0
        df["區間最高價"] = 0.0
        
        # 嘗試補上股票名稱
        for index, row in df.iterrows():
            contract = api.Contracts.Stocks.get(row['代碼'])
            if contract:
                df.at[index, '名稱'] = contract.name
                
        return df
    except Exception as e:
        log(f"取得庫存失敗: {str(e)}")
        return pd.DataFrame()

def place_sell_order(api, code, quantity, order_type_str, reason):
    """執行賣出下單"""
    try:
        contract = api.Contracts.Stocks.get(code)
        if not contract:
            log(f"錯誤: 找不到代碼 {code} 的合約資訊")
            return

        # 解析 Order Type
        order_type_map = {
            'ROD': constant.OrderType.ROD,
            'IOC': constant.OrderType.IOC,
            'FOK': constant.OrderType.FOK
        }
        ord_type = order_type_map.get(order_type_str, constant.OrderType.ROD)
        
        if order_type_str == 'ROD':
            price_type = constant.StockPriceType.LMT
            price = contract.limit_down
            log(f"下單模式為 ROD，使用跌停價 {price} 以確保成交")
        else:
            price_type = constant.StockPriceType.MKT
            price = 0 # 市價
            log(f"下單模式為 {order_type_str}，使用市價單")

        # 建立 Order 物件
        order = api.Order(
            price=price,
            quantity=int(quantity),
            action=constant.Action.Sell,
            price_type=price_type,
            order_type=ord_type,
            account=api.stock_account
        )

        # 送出委託
        trade = api.place_order(contract, order)
        log(f"【觸發下單】 {reason} | 代碼: {code} | 股數: {quantity} | 模式: {order_type_str}")
        return trade
    except Exception as e:
        log(f"下單失敗 ({code}): {str(e)}")

def get_historical_highs(api, codes, start_date_str):
    """批次取得股票歷史最高價"""
    results = {}
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # 建立進度條
    prog_bar = st.progress(0, text="正在讀取歷史區間最高價...")
    total = len(codes)
    
    for i, code in enumerate(codes):
        try:
            contract = api.Contracts.Stocks.get(code)
            if contract:
                kbars = api.kbars(contract, start=start_date_str, end=today_str)
                df_k = pd.DataFrame({**kbars})
                if not df_k.empty:
                    results[code] = float(df_k['High'].max())
        except Exception:
            pass
        prog_bar.progress((i + 1) / total)
        
    prog_bar.empty()
    prog_bar.empty()
    return results
