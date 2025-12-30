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
        
        # Try Shioaji First
        has_data = False
        df_daily = pd.DataFrame()
        
        try:
            kbars = api.kbars(
                contract, 
                start=start_date.strftime("%Y-%m-%d"), 
                end=end_date.strftime("%Y-%m-%d")
            )
            df = pd.DataFrame({**kbars})
            
            if not df.empty:
                # 轉換為日期格式
                df['ts'] = pd.to_datetime(df['ts'])
                df.set_index('ts', inplace=True)
                
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
                has_data = True
        except Exception as e:
            # st.warning(f"API 抓取失敗: {e}")
            pass

        # Try yfinance Fallback
        if not has_data:
            try:
                import yfinance as yf
                yf_code = f"{code}.TW"
                # st.toast(f"切換至 yfinance 抓取 {yf_code}...")
                df_yf = yf.download(yf_code, start=start_date, end=end_date, progress=False)
                
                if not df_yf.empty:
                     # yfinance 已經是日線，且 Index 是 Datetime
                     # 欄位名稱通常對應，但可能是 MultiIndex (Price, Ticker)
                     if isinstance(df_yf.columns, pd.MultiIndex):
                         df_yf.columns = df_yf.columns.get_level_values(0)
                         
                     df_daily = df_yf[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
                     # yfinance Volume sometimes is 0 for indices, but for stocks it's fine.
                     has_data = True
            except Exception as ex:
                # st.error(f"yfinance 失敗: {ex}")
                pass
        
        if not has_data or df_daily.empty:
            st.warning(f"查無 {code} K 線資料 (來源: API & Yahoo)")
            return

        
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
