import os

# 嘗試載入 .env 檔案 (若 python-dotenv 已安裝)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # 若未安裝 python-dotenv，使用預設值


class Config:
    """
    集中管理所有設定 (V2.0 Clean Code 版)
    ============================================
    所有模組應統一使用 from config import Config
    敏感資訊優先從環境變數讀取，否則使用預設值
    """
    
    # ==========================================
    # 🗄️ 資料庫設定
    # ==========================================
    SQLALCHEMY_DATABASE_URI = os.getenv(
        'DB_URL',
        'mysql+pymysql://root:my_secret_password@localhost:3306/stock_ai_db'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # ==========================================
    # 🤖 AI 模型設定
    # ==========================================
    MODEL_PATH = os.getenv('MODEL_PATH', 'ML_Data/pkl/stock_ai_model.pkl')
    
    # V31 混合策略特徵 (使用比例特徵)
    FEATURES = ['rsi', 'bias', 'macd_hist', 'kd_k', 'bb_width', 
                'volume_ratio', 'foreign_ratio', 'trust_ratio']
    
    # ==========================================
    # 📊 交易參數
    # ==========================================
    BOND_SYMBOL = '00679B'      # 避險債券 ETF
    MARKET_SYMBOL = '0050'      # 大盤指標
    TARGET_THRESHOLD = 0.02     # 漲幅門檻 (2%)
    
    # ==========================================
    # 💰 V32 回測擬真化參數
    # ==========================================
    SLIPPAGE_RATE = 0.002       # 滑價率 (0.2%，買高賣低)
    RISK_FREE_RATE = 0.01       # 年化無風險利率 (1%，用於 Sharpe Ratio)
    
    # ==========================================
    # 🔑 API Keys (優先環境變數)
    # ==========================================
    LINE_CHANNEL_ACCESS_TOKEN = os.getenv(
        'LINE_TOKEN',
        'KBl386t0eh2puuuZsgcrGVU2OHJ/Rbyw/h7hEnb6XcWMDGdzUTVEWooMZjoBQtoyqCOIMnd3KHVeA1HAJ1FPJGU2MfDfakPiVZKwvowT6tT4/ZrnqGe+cC61QqZd5S+upAlpMxpftxi6tubsvFYMZwdB04t89/1O/w1cDnyilFU='
    )
    LINE_CHANNEL_SECRET = os.getenv(
        'LINE_SECRET',
        'd5357cddabb11529890938731af41f95'
    )
    GEMINI_API_KEY = os.getenv(
        'GEMINI_KEY',
        'AIzaSyBg-8fEJcjc6LoTVEJQjvrtvGKziKvfgZQ'
    )