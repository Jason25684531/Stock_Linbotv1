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
    # 🎯 V30/V31 策略參數
    # ==========================================
    V30_VOLUME_THRESHOLD = 3_000_000  # 成交量門檻 (300萬股)
    V30_RSI_LOW = 40                  # RSI 下限
    V30_RSI_HIGH = 70                 # RSI 上限
    V30_STOP_LOSS = 0.10              # 停損比例 (10%)
    V30_TAKE_PROFIT = 0.20            # 停利比例 (20%)
    V30_MAX_HOLD_DAYS = 10            # 最長持有天數
    
    # 技術指標計算參數
    RSI_PERIOD = 14
    MACD_FAST = 12
    MACD_SLOW = 26
    MACD_SIGNAL = 9
    KD_PERIOD = 9
    BB_PERIOD = 20
    BB_STD_MULT = 2.0
    
    # ==========================================
    # 🧠 V33 Phase 2: 進階策略濾網 (預設關閉，Opt-in)
    # ==========================================
    USE_KD_FILTER = False          # KD 黃金交叉濾網
    USE_BB_FILTER = False          # 布林通道壓縮突破濾網
    
    # KD 黃金交叉參數
    KD_GOLDEN_CROSS_K_MIN = 20     # K 值最低門檻
    KD_GOLDEN_CROSS_D_MIN = 20     # D 值最低門檻
    KD_GOLDEN_CROSS_K_OVER_D = True  # K 必須大於 D (黃金交叉)
    
    # 布林通道壓縮突破參數
    BB_SQUEEZE_THRESHOLD = 0.03    # 通道寬度 < 3% 視為壓縮
    BB_BREAKOUT_POSITION = 'upper' # 突破方向: 'upper'(上軌) or 'lower'(下軌)
    
    # ==========================================
    # � V33 Phase 2+: 市場情緒分析與熔斷機制
    # ==========================================
    ENABLE_SENTIMENT_FILTER = False     # 情緒熔斷開關（預設關閉，Opt-in）
    SENTIMENT_THRESHOLD = -0.5          # 情緒分數門檻（-1.0 ~ 1.0，低於此值觸發熔斷）
    SENTIMENT_MOCK_MODE = True          # 開發階段使用模擬數據（避免依賴外部 API）
    
    # ==========================================
    # �🔑 API Keys (優先環境變數)
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