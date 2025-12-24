import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta

def draw_stock_chart(api, code, days=100):
    """
    繪製個股 K 線圖 + 20MA + 60MA
    """
    try:
        contract = api.Contracts.Stocks.get(code)
        if not contract:
            st.error(f"找不到代碼 {code} 的合約")
            return

        # 抓取資料：為了計算 60MA，需要抓比 100 天更多的資料 (例如 250 天)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=250) 
        
        kbars = api.kbars(
            contract, 
            start=start_date.strftime("%Y-%m-%d"), 
            end=end_date.strftime("%Y-%m-%d")
        )
        df = pd.DataFrame({**kbars})
        
        if df.empty:
            st.warning("查無 K 線資料")
            return
            
        # 轉換為日期格式
        df['ts'] = pd.to_datetime(df['ts'])
        df.set_index('ts', inplace=True)
        
        # --- 關鍵修正：將分鐘線 Resample 為日線 ---
        # Shioaji API 回傳的是分鐘資料，若要看日 K 需自行轉為日頻率
        df_daily = df.resample('D').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        })
        # 移除沒有交易的日期 (假日)
        df_daily.dropna(subset=['Open'], inplace=True)
        
        # 計算 MA
        df_daily['MA60'] = df_daily['Close'].rolling(window=60).mean()
        df_daily['MA20'] = df_daily['Close'].rolling(window=20).mean()
        
        # 只取最近 N 天顯示
        df_display = df_daily.tail(days)
        
        # 繪圖
        fig = make_subplots(rows=1, cols=1, shared_xaxes=True, vertical_spacing=0.05)

        # K線 (Candlestick)
        fig.add_trace(go.Candlestick(
            x=df_display.index,
            open=df_display['Open'],
            high=df_display['High'],
            low=df_display['Low'],
            close=df_display['Close'],
            name='日 K 線',
            increasing_line_color='red', decreasing_line_color='green' # 台股習慣：紅漲綠跌
        ))

        # 20MA
        fig.add_trace(go.Scatter(
            x=df_display.index,
            y=df_display['MA20'],
            mode='lines',
            name='MA20',
            line=dict(color='cyan', width=1.5)
        ))

        # 60MA
        fig.add_trace(go.Scatter(
            x=df_display.index,
            y=df_display['MA60'],
            mode='lines',
            name='MA60',
            line=dict(color='orange', width=1.5)
        ))

        fig.update_layout(
            title=f"{code} {contract.name} - 近 {days} 日走勢",
            xaxis_title="日期",
            yaxis_title="價格",
            xaxis_rangeslider_visible=False,
            height=500,
            template="plotly_dark"
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
    except Exception as e:
        st.error(f"繪圖發生錯誤: {e}")
