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
    
    # 🔥 V33 Phase 2: 動態策略載入
    # FEATURES, TARGET_RETURN, LOOK_AHEAD_DAYS 現在從 StrategyManager 動態取得
    # 保留 V31 預設值以供向後兼容
    FEATURES = ['rsi', 'bias', 'macd_hist', 'kd_k', 'bb_width', 
                'volume_ratio', 'foreign_ratio', 'trust_ratio']
    
    @classmethod
    def get_active_features(cls):
        """動態取得當前策略的特徵列表
        
        ⚠️ 使用 Lazy Loading 避免循環依賴
        
        Returns:
            List[str]: 特徵名稱列表
        """
        try:
            from tool.strategy_manager import get_active_strategy
            strategy = get_active_strategy()
            return strategy.features
        except Exception as e:
            # 回退到預設值
            print(f"⚠️ 無法載入策略特徵，使用預設值: {e}")
            return cls.FEATURES
    
    @classmethod
    def get_target_return(cls):
        """動態取得當前策略的目標報酬率
        
        Returns:
            float: 目標報酬率
        """
        try:
            from tool.strategy_manager import get_active_strategy
            strategy = get_active_strategy()
            return strategy.target_return
        except Exception as e:
            # 回退到預設值
            return 0.08
    
    @classmethod
    def get_look_ahead_days(cls):
        """動態取得當前策略的預測天數
        
        Returns:
            int: 預測天數
        """
        try:
            from tool.strategy_manager import get_active_strategy
            strategy = get_active_strategy()
            return strategy.look_ahead_days
        except Exception as e:
            # 回退到預設值
            return 7
    
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
    V30_STOP_LOSS = 0.07              # 停損比例 (7%) 🔥 收緊以降低 MDD
    V30_TAKE_PROFIT = 0.15            # 停利比例 (15%) 🔥 收緊以提早獲利
    V30_MAX_HOLD_DAYS = 10            # 最長持有天數
    
    # V30 參數字典（用於向後兼容和便捷存取）
    V30_PARAMS = {
        'VOLUME_THRESHOLD': 3_000_000,
        'RSI_LOW': 40,
        'RSI_HIGH': 70,
        'STOP_LOSS': 0.07,              # 🔥 收緊停損至 7%
        'TAKE_PROFIT': 0.15,            # 🔥 收緊停利至 15%
        'MAX_HOLD_DAYS': 10
    }
    
    # 技術指標計算參數
    RSI_PERIOD = 14
    MACD_FAST = 12
    MACD_SLOW = 26
    MACD_SIGNAL = 9
    KD_PERIOD = 9
    BB_PERIOD = 20
    BB_STD_MULT = 2.0
    
    # ==========================================
    # 🧠 V33 Phase 2: 進階策略濾網（全面啟用以降低 MDD）
    # ==========================================
    USE_MARKET_FILTER = True       # 🔥 市場熔斷機制：大盤 < MA60 禁止買入
    USE_TREND_FILTER = True        # 🔥 個股趨勢濾網：收盤 > MA60
    USE_KD_FILTER = True           # 🔥 KD 超賣反彈濾網：K<30 且 K>D
    USE_BB_FILTER = False          # 布林通道壓縮突破濾網（保持關閉）
    
    # KD 黃金交叉參數
    KD_GOLDEN_CROSS_K_MIN = 20     # K 值最低門檻
    KD_GOLDEN_CROSS_D_MIN = 20     # D 值最低門檻
    KD_GOLDEN_CROSS_K_OVER_D = True  # K 必須大於 D (黃金交叉)
    
    # 布林通道壓縮突破參數
    BB_SQUEEZE_THRESHOLD = 0.03    # 通道寬度 < 3% 視為壓縮
    BB_BREAKOUT_POSITION = 'upper' # 突破方向: 'upper'(上軌) or 'lower'(下軌)
    
    # ==========================================
    # 🛡️ V33 Phase 1+: ATR 動態停損
    # ==========================================
    USE_ATR_STOP = True             # 🔥 啟用 ATR 動態停損（波動大則寬，波動小則窄）
    ATR_MULTIPLIER = 2.0            # 停損 = 收盤價 - ATR * 2.0
    ATR_PERIOD = 14                 # ATR 計算週期

    # ==========================================
    # 🚀 V34 策略門檻（可調參）
    # ==========================================
    V34_REVENUE_YOY_MIN = float(os.getenv('V34_REVENUE_YOY_MIN', '18.0'))
    V34_BREAKOUT_RATIO = float(os.getenv('V34_BREAKOUT_RATIO', '0.93'))
    V34_VOLUME_RATIO_MIN = float(os.getenv('V34_VOLUME_RATIO_MIN', '0.9'))

    # V34 空集合時啟用的放寬參數
    V34_RELAXED_REVENUE_YOY_MIN = float(os.getenv('V34_RELAXED_REVENUE_YOY_MIN', '10.0'))
    V34_RELAXED_BREAKOUT_RATIO = float(os.getenv('V34_RELAXED_BREAKOUT_RATIO', '0.90'))
    V34_RELAXED_VOLUME_RATIO_MIN = float(os.getenv('V34_RELAXED_VOLUME_RATIO_MIN', '0.7'))

    # ==========================================
    # 💼 V35 策略門檻（可調參）
    # ==========================================
    V35_OP_MARGIN_MIN = float(os.getenv('V35_OP_MARGIN_MIN', '0.06'))
    V35_REVENUE_YOY_MIN = float(os.getenv('V35_REVENUE_YOY_MIN', '0.0'))
    V35_VOLUME_RATIO_MIN = float(os.getenv('V35_VOLUME_RATIO_MIN', '0.8'))

    # V35 空集合時啟用的放寬參數
    V35_RELAXED_OP_MARGIN_MIN = float(os.getenv('V35_RELAXED_OP_MARGIN_MIN', '0.04'))
    V35_RELAXED_REVENUE_YOY_MIN = float(os.getenv('V35_RELAXED_REVENUE_YOY_MIN', '-5.0'))
    V35_RELAXED_VOLUME_RATIO_MIN = float(os.getenv('V35_RELAXED_VOLUME_RATIO_MIN', '0.6'))
    
    # ==========================================
    # � V33 Phase 2+: 市場情緒分析與熔斷機制
    # ==========================================
    ENABLE_SENTIMENT_FILTER = False     # 情緒熔斷開關（預設關閉，Opt-in）
    SENTIMENT_THRESHOLD = -0.5          # 情緒分數門檻（-1.0 ~ 1.0，低於此值觸發熔斷）
    SENTIMENT_MOCK_MODE = True          # 開發階段使用模擬數據（避免依賴外部 API）
    
    # ==========================================
    # 🔑 API Keys (從環境變數讀取 - 無預設值)
    # ==========================================
    LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_TOKEN', '')
    LINE_CHANNEL_SECRET = os.getenv('LINE_SECRET', '')
    GEMINI_API_KEY = os.getenv('GEMINI_KEY', '')
    
    # 🔐 Web Dashboard 驗證 (Phase 1 Security)
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin123')
    FLASK_SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')