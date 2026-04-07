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
    # 🌐 MCP 傳輸設定
    # ==========================================
    MCP_BASE_URL = os.getenv(
        'MCP_BASE_URL',
        'http://localhost:8080'
    ).rstrip('/')
    MCP_DEFAULT_MARKET = os.getenv('MCP_DEFAULT_MARKET', 'ALL')
    MCP_HTTP_TIMEOUT_SECONDS = float(
        os.getenv('MCP_HTTP_TIMEOUT_SECONDS', '20')
    )
    MCP_CONNECT_TIMEOUT_SECONDS = float(
        os.getenv('MCP_CONNECT_TIMEOUT_SECONDS', '5')
    )
    MCP_MAX_RETRIES = int(os.getenv('MCP_MAX_RETRIES', '3'))
    MCP_BACKOFF_BASE_SECONDS = float(
        os.getenv('MCP_BACKOFF_BASE_SECONDS', '1.0')
    )
    MCP_MAX_BACKOFF_SECONDS = float(
        os.getenv('MCP_MAX_BACKOFF_SECONDS', '8.0')
    )
    MCP_HEALTH_PATH = os.getenv('MCP_HEALTH_PATH', '/health')
    APP_HEALTH_PATH = os.getenv('APP_HEALTH_PATH', '/health')
    
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
    MARKET_SYMBOL = '2330'      # 大盤指標（台積電，與加權指數高度連動）
    TARGET_THRESHOLD = 0.02     # 漲幅門檻 (2%)
    
    # ==========================================
    # 💰 V32 回測擬真化參數
    # ==========================================
    FEE_RATE = 0.001425         # 台股手續費率 (0.1425%)
    TAX_RATE = 0.003            # 台股證交稅率 (0.3%，賣出收取)
    SLIPPAGE_RATE = 0.002       # 滑價率 (0.2%，買高賣低)
    RISK_FREE_RATE = 0.01       # 年化無風險利率 (1%，用於 Sharpe Ratio)
    TRAIN_RATIO = 0.8           # 訓練集比例 (80% 訓練 / 20% 測試)
    BACKTEST_MIN_PRICE = 10     # 回測最低股價篩選
    BACKTEST_MAX_PRICE = 500    # 回測最高股價篩選
    
    # ==========================================
    # 🎯 V30/V31 策略參數
    # ==========================================
    V30_VOLUME_THRESHOLD = 3_000_000  # 成交量門檻 (300萬股)
    V30_RSI_LOW = 40                  # RSI 下限
    V30_RSI_HIGH = 70                 # RSI 上限
    V30_STOP_LOSS = 0.07              # 停損比例 (7%) 🔥 收緊以降低 MDD
    V30_TAKE_PROFIT = 0.15            # 停利比例 (15%) 🔥 收緊以提早獲利
    V30_MAX_HOLD_DAYS = 10            # 最長持有天數
    
    # V30 參數字典（唯一入口，引用 class 屬性）
    @classmethod
    def get_v30_params(cls):
        return {
            'VOLUME_THRESHOLD': cls.V30_VOLUME_THRESHOLD,
            'RSI_LOW': cls.V30_RSI_LOW,
            'RSI_HIGH': cls.V30_RSI_HIGH,
            'STOP_LOSS': cls.V30_STOP_LOSS,
            'TAKE_PROFIT': cls.V30_TAKE_PROFIT,
            'MAX_HOLD_DAYS': cls.V30_MAX_HOLD_DAYS,
        }

    # 向後相容屬性（委派至 get_v30_params classmethod）
    class _V30ParamsProxy(dict):
        """延遲讀取 V30 參數的 dict 代理，確保引用 class 屬性而非靜態硬編碼值"""
        def __getitem__(self, key):
            return Config.get_v30_params()[key]
        def get(self, key, default=None):
            return Config.get_v30_params().get(key, default)
        def __repr__(self):
            return repr(Config.get_v30_params())

    V30_PARAMS = _V30ParamsProxy()
    
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
    RECOMMENDATION_FALLBACK_MAX_AGE_DAYS = int(os.getenv('RECOMMENDATION_FALLBACK_MAX_AGE_DAYS', '7'))
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
    # 📉 V33 低波動策略門檻（可調參）
    # ==========================================
    V33_VOLUME_THRESHOLD = 5_000_000   # 成交股數門檻
    V33_VOLUME_RATIO_MIN = 1.0         # 量比最低要求
    V33_NATR_MAX = 3.5                 # NATR 上限 (%)
    V33_RSI_LOW = 45                   # RSI 下限
    V33_RSI_HIGH = 65                  # RSI 上限
    V33_MACD_HIST_MIN = -0.2           # MACD Histogram 最低值
    V33_BIAS_LOW = -6.0                # 乖離率下限 (%)
    V33_BIAS_HIGH = 12.0               # 乖離率上限 (%)
    
    # ==========================================
    # � 籌碼面指標常數 (Phase 2)
    # ==========================================
    # 外資/投信/自營商連續買超天數計算
    CHIP_CONSEC_DAYS_WINDOW = 60       # 用於計算連續天數的回看視窗

    # chip_score 綜合分數的權重
    CHIP_WEIGHT_FOREIGN = 0.4          # 外資買超信號權重
    CHIP_WEIGHT_TRUST = 0.3            # 投信買超信號權重
    CHIP_WEIGHT_DEALER = 0.15          # 自營商買超信號權重
    CHIP_WEIGHT_MARGIN = 0.15          # 融資融券信號權重

    # 融資使用率警戒線
    CHIP_MARGIN_DANGER_RATIO = 0.8     # 融資使用率 > 80% 視為高風險

    # ==========================================
    # �🛡️ V33 Phase 1+: ATR 動態停損
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
    V34_VOLUME_MIN = int(os.getenv('V34_VOLUME_MIN', '300'))

    # V34 空集合時啟用的放寬參數
    V34_RELAXED_REVENUE_YOY_MIN = float(os.getenv('V34_RELAXED_REVENUE_YOY_MIN', '10.0'))
    V34_RELAXED_BREAKOUT_RATIO = float(os.getenv('V34_RELAXED_BREAKOUT_RATIO', '0.90'))
    V34_RELAXED_VOLUME_RATIO_MIN = float(os.getenv('V34_RELAXED_VOLUME_RATIO_MIN', '0.7'))
    V34_RELAXED_VOLUME_MIN = int(os.getenv('V34_RELAXED_VOLUME_MIN', '150'))

    # ==========================================
    # 💼 V35 策略門檻（可調參）
    # ==========================================
    V35_OP_MARGIN_MIN = float(os.getenv('V35_OP_MARGIN_MIN', '0.06'))
    V35_REVENUE_YOY_MIN = float(os.getenv('V35_REVENUE_YOY_MIN', '0.0'))
    V35_VOLUME_RATIO_MIN = float(os.getenv('V35_VOLUME_RATIO_MIN', '0.8'))
    V35_VOLUME_MIN = int(os.getenv('V35_VOLUME_MIN', '300'))

    # V35 空集合時啟用的放寬參數
    V35_RELAXED_OP_MARGIN_MIN = float(os.getenv('V35_RELAXED_OP_MARGIN_MIN', '0.04'))
    V35_RELAXED_REVENUE_YOY_MIN = float(os.getenv('V35_RELAXED_REVENUE_YOY_MIN', '-5.0'))
    V35_RELAXED_VOLUME_RATIO_MIN = float(os.getenv('V35_RELAXED_VOLUME_RATIO_MIN', '0.6'))
    V35_RELAXED_VOLUME_MIN = int(os.getenv('V35_RELAXED_VOLUME_MIN', '150'))

    # ==========================================
    # 📊 V36 籌碼動能策略門檻（可調參）
    # ==========================================
    V36_CHIP_SCORE_MIN = float(os.getenv('V36_CHIP_SCORE_MIN', '55'))       # chip_score 最低門檻
    V36_FOREIGN_CONSEC_MIN = int(os.getenv('V36_FOREIGN_CONSEC_MIN', '3'))  # 外資連買最低天數
    V36_TRUST_CONSEC_MIN = int(os.getenv('V36_TRUST_CONSEC_MIN', '2'))      # 投信連買最低天數
    V36_VOLUME_THRESHOLD = int(os.getenv('V36_VOLUME_THRESHOLD', '500'))     # 最低成交量（張）
    V36_VOLUME_RATIO_MIN = float(os.getenv('V36_VOLUME_RATIO_MIN', '0.8'))  # 量比最低門檻
    V36_RSI_LOW = float(os.getenv('V36_RSI_LOW', '40'))                     # RSI 下限
    V36_RSI_HIGH = float(os.getenv('V36_RSI_HIGH', '80'))                   # RSI 上限
    V36_BIAS_HIGH = float(os.getenv('V36_BIAS_HIGH', '15'))                 # 乖離率上限
    V36_STOP_LOSS = float(os.getenv('V36_STOP_LOSS', '0.07'))               # 停損比例
    V36_TAKE_PROFIT = float(os.getenv('V36_TAKE_PROFIT', '0.15'))           # 停利比例
    V36_MAX_HOLD_DAYS = int(os.getenv('V36_MAX_HOLD_DAYS', '12'))           # 最大持有天數

    # ==========================================
    # � V37 均值回歸策略門檻（可調參）
    # ==========================================
    V37_KD_LOW = float(os.getenv('V37_KD_LOW', '35'))                        # KD 超賣門檻
    V37_BB_WIDTH_MAX = float(os.getenv('V37_BB_WIDTH_MAX', '15'))            # BB 寬度上限（收斂判斷）
    V37_BIAS_LOW = float(os.getenv('V37_BIAS_LOW', '-8'))                    # 乖離率下限
    V37_BIAS_HIGH = float(os.getenv('V37_BIAS_HIGH', '3'))                   # 乖離率上限
    V37_VOLUME_RATIO_MAX = float(os.getenv('V37_VOLUME_RATIO_MAX', '1.0'))   # 量比上限（量縮確認）
    V37_RSI_LOW = float(os.getenv('V37_RSI_LOW', '30'))                      # RSI 下限
    V37_RSI_HIGH = float(os.getenv('V37_RSI_HIGH', '55'))                    # RSI 上限
    V37_VOLUME_THRESHOLD = int(os.getenv('V37_VOLUME_THRESHOLD', '500'))     # 最低成交量（張）
    V37_STOP_LOSS = float(os.getenv('V37_STOP_LOSS', '0.05'))                # 停損比例 5%
    V37_TAKE_PROFIT = float(os.getenv('V37_TAKE_PROFIT', '0.10'))            # 停利比例 10%
    V37_MAX_HOLD_DAYS = int(os.getenv('V37_MAX_HOLD_DAYS', '8'))             # 最大持有天數

    # ==========================================
    # 💰 V38 高殖利率價值策略門檻（可調參）
    # ==========================================
    V38_OP_MARGIN_MIN = float(os.getenv('V38_OP_MARGIN_MIN', '0.08'))        # 營業利益率最低門檻 8%
    V38_EPS_MIN = float(os.getenv('V38_EPS_MIN', '0'))                       # EPS 最低門檻 > 0
    V38_NATR_MAX = float(os.getenv('V38_NATR_MAX', '4.0'))                   # NATR 上限（低波動）
    V38_STD20_MAX = float(os.getenv('V38_STD20_MAX', '3.0'))                 # STD_20 上限
    V38_RSI_LOW = float(os.getenv('V38_RSI_LOW', '40'))                      # RSI 下限
    V38_RSI_HIGH = float(os.getenv('V38_RSI_HIGH', '65'))                    # RSI 上限
    V38_BIAS_LOW = float(os.getenv('V38_BIAS_LOW', '-5'))                    # 乖離率下限
    V38_BIAS_HIGH = float(os.getenv('V38_BIAS_HIGH', '8'))                   # 乖離率上限
    V38_VOLUME_THRESHOLD = int(os.getenv('V38_VOLUME_THRESHOLD', '300'))     # 最低成交量（張）
    V38_STOP_LOSS = float(os.getenv('V38_STOP_LOSS', '0.06'))                # 停損比例 6%
    V38_TAKE_PROFIT = float(os.getenv('V38_TAKE_PROFIT', '0.12'))            # 停利比例 12%
    V38_MAX_HOLD_DAYS = int(os.getenv('V38_MAX_HOLD_DAYS', '15'))            # 最大持有天數

    # ==========================================

    # 🔑 API Keys (從環境變數讀取 - 無預設值)
    # ==========================================
    LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_TOKEN', '')
    LINE_CHANNEL_SECRET = os.getenv('LINE_SECRET', '')
    GEMINI_API_KEY = os.getenv('GEMINI_KEY', '')

    # 📰 新聞情緒加分／減分（雙向情緒影響）
    NEWS_BOOST_ENABLED = True       # 新聞族群加分開關
    NEWS_BOOST_FACTOR = 0.10        # 利多族群加分幅度 10%
    NEWS_BOOST_MAX = 0.15           # 加分上限 15%（族群 + 個股合計）
    NEWS_PENALTY_FACTOR = 0.10      # 利空族群折減幅度 10%
    NEWS_BEAR_MAX_HOLDINGS = 2      # 偏空市場時最大持股上限（原本為 3）
    
    # 🔐 Web Dashboard 驗證 (Phase 1 Security)
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin123')
    FLASK_SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')

    # 🌐 Dashboard 公開 URL（Line Bot 回覆用）
    # 若有設 PUBLIC_DASHBOARD_URL 環境變數（如 ngrok 固定 URL）則優先使用
    # 未設定時預設 localhost；若 ngrok 在運行則由 get_ngrok_url() 動態偵測
    PUBLIC_DASHBOARD_URL = os.getenv('PUBLIC_DASHBOARD_URL', 'http://localhost:1688')


# ==========================================
# 📊 V34/V35 模式預設組合（積極 / 平衡 / 寬鬆 / 穩健）
# ==========================================

V34_MODE_PRESETS = {
    'aggressive': {
        'v34_revenue_yoy_min': '14.0',
        'v34_breakout_ratio': '0.91',
        'v34_volume_ratio_min': '0.75',
        'v34_volume_min': '250',
        'v34_relaxed_revenue_yoy_min': '6.0',
        'v34_relaxed_breakout_ratio': '0.88',
        'v34_relaxed_volume_ratio_min': '0.55',
        'v34_relaxed_volume_min': '120',
    },
    'balanced': {
        'v34_revenue_yoy_min': '18.0',
        'v34_breakout_ratio': '0.93',
        'v34_volume_ratio_min': '0.90',
        'v34_volume_min': '300',
        'v34_relaxed_revenue_yoy_min': '10.0',
        'v34_relaxed_breakout_ratio': '0.90',
        'v34_relaxed_volume_ratio_min': '0.70',
        'v34_relaxed_volume_min': '150',
    },
    'loose': {
        'v34_revenue_yoy_min': '8.0',
        'v34_breakout_ratio': '0.87',
        'v34_volume_ratio_min': '0.50',
        'v34_volume_min': '120',
        'v34_relaxed_revenue_yoy_min': '0.0',
        'v34_relaxed_breakout_ratio': '0.84',
        'v34_relaxed_volume_ratio_min': '0.30',
        'v34_relaxed_volume_min': '60',
    },
    'conservative': {
        'v34_revenue_yoy_min': '22.0',
        'v34_breakout_ratio': '0.96',
        'v34_volume_ratio_min': '1.00',
        'v34_volume_min': '500',
        'v34_relaxed_revenue_yoy_min': '14.0',
        'v34_relaxed_breakout_ratio': '0.92',
        'v34_relaxed_volume_ratio_min': '0.80',
        'v34_relaxed_volume_min': '300',
    },
}

V35_MODE_PRESETS = {
    'aggressive': {
        'v35_op_margin_min': '0.05',
        'v35_revenue_yoy_min': '-2.0',
        'v35_volume_ratio_min': '0.70',
        'v35_volume_min': '250',
        'v35_relaxed_op_margin_min': '0.03',
        'v35_relaxed_revenue_yoy_min': '-8.0',
        'v35_relaxed_volume_ratio_min': '0.50',
        'v35_relaxed_volume_min': '120',
    },
    'balanced': {
        'v35_op_margin_min': '0.06',
        'v35_revenue_yoy_min': '0.0',
        'v35_volume_ratio_min': '0.80',
        'v35_volume_min': '300',
        'v35_relaxed_op_margin_min': '0.04',
        'v35_relaxed_revenue_yoy_min': '-5.0',
        'v35_relaxed_volume_ratio_min': '0.60',
        'v35_relaxed_volume_min': '150',
    },
    'loose': {
        'v35_op_margin_min': '0.03',
        'v35_revenue_yoy_min': '-10.0',
        'v35_volume_ratio_min': '0.50',
        'v35_volume_min': '120',
        'v35_relaxed_op_margin_min': '0.01',
        'v35_relaxed_revenue_yoy_min': '-20.0',
        'v35_relaxed_volume_ratio_min': '0.30',
        'v35_relaxed_volume_min': '60',
    },
    'conservative': {
        'v35_op_margin_min': '0.08',
        'v35_revenue_yoy_min': '3.0',
        'v35_volume_ratio_min': '0.90',
        'v35_volume_min': '500',
        'v35_relaxed_op_margin_min': '0.05',
        'v35_relaxed_revenue_yoy_min': '0.0',
        'v35_relaxed_volume_ratio_min': '0.70',
        'v35_relaxed_volume_min': '300',
    },
}

# 模式切換表：映射 compact key → (mode_label, preset_key)
MODE_CMD_MAP = {
    '切換積極': ('積極', 'aggressive'),
    '積極': ('積極', 'aggressive'),
    '切積極': ('積極', 'aggressive'),
    '切換平衡': ('平衡', 'balanced'),
    '平衡': ('平衡', 'balanced'),
    '切平衡': ('平衡', 'balanced'),
    '切換寬鬆': ('寬鬆', 'loose'),
    '寬鬆': ('寬鬆', 'loose'),
    '切寬鬆': ('寬鬆', 'loose'),
    '切換穩健': ('穩健', 'conservative'),
}

MODE_EMOJI = {
    'aggressive': '😈',
    'balanced': '⚖️',
    'loose': '🌊',
    'conservative': '🛡️',
}

MODE_REPLY_TEMPLATE = {
    'aggressive': '已切換至【積極模式】\nV34/V35 已同步放寬（嚴格與放寬門檻）',
    'balanced': '已切換至【平衡模式】\nV34/V35 已同步套用平衡門檻',
    'loose': '已切換至【寬鬆模式】\nV34/V35 已同步放寬，增加可選股票數',
    'conservative': '已切換至【穩健模式】(相容模式)',
}