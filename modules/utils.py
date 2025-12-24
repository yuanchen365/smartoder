import streamlit as st
from datetime import datetime

def log(message):
    """寫入日誌到 Session State，並加上時間戳記"""
    if 'log_messages' not in st.session_state:
        st.session_state.log_messages = []
        
    timestamp = datetime.now().strftime("%H:%M:%S")
    full_msg = f"[{timestamp}] {message}"
    st.session_state.log_messages.insert(0, full_msg) # 新的訊息在最上面
    # 限制日誌長度，避免記憶體爆掉
    if len(st.session_state.log_messages) > 100:
        st.session_state.log_messages.pop()
