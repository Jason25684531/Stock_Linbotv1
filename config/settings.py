"""Canonical configuration and settings definitions for the project."""

from __future__ import annotations

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    """Centralized runtime configuration surface."""

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DB_URL",
        "mysql+pymysql://root:my_secret_password@localhost:3306/stock_ai_db",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    MCP_BASE_URL = os.getenv("MCP_BASE_URL", "http://localhost:8080").rstrip("/")
    MCP_DEFAULT_MARKET = os.getenv("MCP_DEFAULT_MARKET", "ALL")
    MCP_HTTP_TIMEOUT_SECONDS = float(os.getenv("MCP_HTTP_TIMEOUT_SECONDS", "20"))
    MCP_CONNECT_TIMEOUT_SECONDS = float(os.getenv("MCP_CONNECT_TIMEOUT_SECONDS", "5"))
    MCP_MAX_RETRIES = int(os.getenv("MCP_MAX_RETRIES", "3"))
    MCP_BACKOFF_BASE_SECONDS = float(os.getenv("MCP_BACKOFF_BASE_SECONDS", "1.0"))
    MCP_MAX_BACKOFF_SECONDS = float(os.getenv("MCP_MAX_BACKOFF_SECONDS", "8.0"))
    MCP_HEALTH_PATH = os.getenv("MCP_HEALTH_PATH", "/health")
    APP_HEALTH_PATH = os.getenv("APP_HEALTH_PATH", "/health")

    MODEL_PATH = os.getenv("MODEL_PATH", "ML_Data/pkl/stock_ai_model.pkl")

    FEATURES = [
        "rsi",
        "bias",
        "macd_hist",
        "kd_k",
        "bb_width",
        "volume_ratio",
        "foreign_ratio",
        "trust_ratio",
    ]

    @classmethod
    def get_active_features(cls):
        """Dynamically resolve the active strategy feature list."""
        try:
            from core.strategy_manager import get_active_strategy

            strategy = get_active_strategy()
            return strategy.features
        except Exception as exc:
            print(f"⚠️ 無法載入策略特徵，使用預設值: {exc}")
            return cls.FEATURES

    @classmethod
    def get_target_return(cls):
        """Dynamically resolve the active strategy target return."""
        try:
            from core.strategy_manager import get_active_strategy

            strategy = get_active_strategy()
            return strategy.target_return
        except Exception:
            return 0.08

    @classmethod
    def get_look_ahead_days(cls):
        """Dynamically resolve the active strategy look-ahead window."""
        try:
            from core.strategy_manager import get_active_strategy

            strategy = get_active_strategy()
            return strategy.look_ahead_days
        except Exception:
            return 7

    @classmethod
    def is_news_boost_enabled(cls) -> bool:
        return _env_flag("NEWS_BOOST_ENABLED", cls.NEWS_BOOST_ENABLED)

    BOND_SYMBOL = "00679B"
    MARKET_SYMBOL = "2330"
    TARGET_THRESHOLD = 0.02

    FEE_RATE = 0.001425
    TAX_RATE = 0.003
    SLIPPAGE_RATE = 0.002
    RISK_FREE_RATE = 0.01
    TRAIN_RATIO = 0.8
    BACKTEST_MIN_PRICE = 10
    BACKTEST_MAX_PRICE = 500

    V30_VOLUME_THRESHOLD = 3_000_000
    V30_RSI_LOW = 40
    V30_RSI_HIGH = 70
    V30_STOP_LOSS = 0.07
    V30_TAKE_PROFIT = 0.15
    V30_MAX_HOLD_DAYS = 10

    @classmethod
    def get_v30_params(cls):
        return {
            "VOLUME_THRESHOLD": cls.V30_VOLUME_THRESHOLD,
            "RSI_LOW": cls.V30_RSI_LOW,
            "RSI_HIGH": cls.V30_RSI_HIGH,
            "STOP_LOSS": cls.V30_STOP_LOSS,
            "TAKE_PROFIT": cls.V30_TAKE_PROFIT,
            "MAX_HOLD_DAYS": cls.V30_MAX_HOLD_DAYS,
        }

    class _V30ParamsProxy(dict):
        """Lazy proxy that always reflects the latest V30 class attributes."""

        def __getitem__(self, key):
            return Config.get_v30_params()[key]

        def get(self, key, default=None):
            return Config.get_v30_params().get(key, default)

        def __repr__(self):
            return repr(Config.get_v30_params())

    V30_PARAMS = _V30ParamsProxy()

    RSI_PERIOD = 14
    MACD_FAST = 12
    MACD_SLOW = 26
    MACD_SIGNAL = 9
    KD_PERIOD = 9
    BB_PERIOD = 20
    BB_STD_MULT = 2.0

    USE_MARKET_FILTER = True
    USE_TREND_FILTER = True
    RECOMMENDATION_FALLBACK_MAX_AGE_DAYS = int(
        os.getenv("RECOMMENDATION_FALLBACK_MAX_AGE_DAYS", "7")
    )
    USE_KD_FILTER = True
    USE_BB_FILTER = False

    KD_GOLDEN_CROSS_K_MIN = 20
    KD_GOLDEN_CROSS_D_MIN = 20
    KD_GOLDEN_CROSS_K_OVER_D = True

    BB_SQUEEZE_THRESHOLD = 0.03
    BB_BREAKOUT_POSITION = "upper"

    V33_VOLUME_THRESHOLD = 5_000_000
    V33_VOLUME_RATIO_MIN = 1.0
    V33_NATR_MAX = 3.5
    V33_RSI_LOW = 45
    V33_RSI_HIGH = 65
    V33_MACD_HIST_MIN = -0.2
    V33_BIAS_LOW = -6.0
    V33_BIAS_HIGH = 12.0

    CHIP_CONSEC_DAYS_WINDOW = 60
    CHIP_WEIGHT_FOREIGN = 0.4
    CHIP_WEIGHT_TRUST = 0.3
    CHIP_WEIGHT_DEALER = 0.15
    CHIP_WEIGHT_MARGIN = 0.15
    CHIP_MARGIN_DANGER_RATIO = 0.8

    USE_ATR_STOP = True
    ATR_MULTIPLIER = 2.0
    ATR_PERIOD = 14

    V34_REVENUE_YOY_MIN = float(os.getenv("V34_REVENUE_YOY_MIN", "18.0"))
    V34_BREAKOUT_RATIO = float(os.getenv("V34_BREAKOUT_RATIO", "0.93"))
    V34_VOLUME_RATIO_MIN = float(os.getenv("V34_VOLUME_RATIO_MIN", "0.9"))
    V34_VOLUME_MIN = int(os.getenv("V34_VOLUME_MIN", "300"))
    V34_RELAXED_REVENUE_YOY_MIN = float(
        os.getenv("V34_RELAXED_REVENUE_YOY_MIN", "10.0")
    )
    V34_RELAXED_BREAKOUT_RATIO = float(
        os.getenv("V34_RELAXED_BREAKOUT_RATIO", "0.90")
    )
    V34_RELAXED_VOLUME_RATIO_MIN = float(
        os.getenv("V34_RELAXED_VOLUME_RATIO_MIN", "0.7")
    )
    V34_RELAXED_VOLUME_MIN = int(os.getenv("V34_RELAXED_VOLUME_MIN", "150"))

    V35_OP_MARGIN_MIN = float(os.getenv("V35_OP_MARGIN_MIN", "0.06"))
    V35_REVENUE_YOY_MIN = float(os.getenv("V35_REVENUE_YOY_MIN", "0.0"))
    V35_VOLUME_RATIO_MIN = float(os.getenv("V35_VOLUME_RATIO_MIN", "0.8"))
    V35_VOLUME_MIN = int(os.getenv("V35_VOLUME_MIN", "300"))
    V35_RELAXED_OP_MARGIN_MIN = float(
        os.getenv("V35_RELAXED_OP_MARGIN_MIN", "0.04")
    )
    V35_RELAXED_REVENUE_YOY_MIN = float(
        os.getenv("V35_RELAXED_REVENUE_YOY_MIN", "-5.0")
    )
    V35_RELAXED_VOLUME_RATIO_MIN = float(
        os.getenv("V35_RELAXED_VOLUME_RATIO_MIN", "0.6")
    )
    V35_RELAXED_VOLUME_MIN = int(os.getenv("V35_RELAXED_VOLUME_MIN", "150"))

    V36_CHIP_SCORE_MIN = float(os.getenv("V36_CHIP_SCORE_MIN", "55"))
    V36_FOREIGN_CONSEC_MIN = int(os.getenv("V36_FOREIGN_CONSEC_MIN", "3"))
    V36_TRUST_CONSEC_MIN = int(os.getenv("V36_TRUST_CONSEC_MIN", "2"))
    V36_VOLUME_THRESHOLD = int(os.getenv("V36_VOLUME_THRESHOLD", "500"))
    V36_VOLUME_RATIO_MIN = float(os.getenv("V36_VOLUME_RATIO_MIN", "0.8"))
    V36_RSI_LOW = float(os.getenv("V36_RSI_LOW", "40"))
    V36_RSI_HIGH = float(os.getenv("V36_RSI_HIGH", "80"))
    V36_BIAS_HIGH = float(os.getenv("V36_BIAS_HIGH", "15"))
    V36_STOP_LOSS = float(os.getenv("V36_STOP_LOSS", "0.07"))
    V36_TAKE_PROFIT = float(os.getenv("V36_TAKE_PROFIT", "0.15"))
    V36_MAX_HOLD_DAYS = int(os.getenv("V36_MAX_HOLD_DAYS", "12"))

    V37_KD_LOW = float(os.getenv("V37_KD_LOW", "35"))
    V37_BB_WIDTH_MAX = float(os.getenv("V37_BB_WIDTH_MAX", "15"))
    V37_BIAS_LOW = float(os.getenv("V37_BIAS_LOW", "-8"))
    V37_BIAS_HIGH = float(os.getenv("V37_BIAS_HIGH", "3"))
    V37_VOLUME_RATIO_MAX = float(os.getenv("V37_VOLUME_RATIO_MAX", "1.0"))
    V37_RSI_LOW = float(os.getenv("V37_RSI_LOW", "30"))
    V37_RSI_HIGH = float(os.getenv("V37_RSI_HIGH", "55"))
    V37_VOLUME_THRESHOLD = int(os.getenv("V37_VOLUME_THRESHOLD", "500"))
    V37_STOP_LOSS = float(os.getenv("V37_STOP_LOSS", "0.05"))
    V37_TAKE_PROFIT = float(os.getenv("V37_TAKE_PROFIT", "0.10"))
    V37_MAX_HOLD_DAYS = int(os.getenv("V37_MAX_HOLD_DAYS", "8"))

    V38_OP_MARGIN_MIN = float(os.getenv("V38_OP_MARGIN_MIN", "0.08"))
    V38_EPS_MIN = float(os.getenv("V38_EPS_MIN", "0"))
    V38_NATR_MAX = float(os.getenv("V38_NATR_MAX", "4.0"))
    V38_STD20_MAX = float(os.getenv("V38_STD20_MAX", "3.0"))
    V38_RSI_LOW = float(os.getenv("V38_RSI_LOW", "40"))
    V38_RSI_HIGH = float(os.getenv("V38_RSI_HIGH", "65"))
    V38_BIAS_LOW = float(os.getenv("V38_BIAS_LOW", "-5"))
    V38_BIAS_HIGH = float(os.getenv("V38_BIAS_HIGH", "8"))
    V38_VOLUME_THRESHOLD = int(os.getenv("V38_VOLUME_THRESHOLD", "300"))
    V38_STOP_LOSS = float(os.getenv("V38_STOP_LOSS", "0.06"))
    V38_TAKE_PROFIT = float(os.getenv("V38_TAKE_PROFIT", "0.12"))
    V38_MAX_HOLD_DAYS = int(os.getenv("V38_MAX_HOLD_DAYS", "15"))

    LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_TOKEN", "")
    LINE_CHANNEL_SECRET = os.getenv("LINE_SECRET", "")
    GEMINI_API_KEY = os.getenv("GEMINI_KEY", "")

    NEWS_BOOST_ENABLED = _env_flag("NEWS_BOOST_ENABLED", False)
    NEWS_BOOST_FACTOR = 0.10
    NEWS_BOOST_MAX = 0.15
    NEWS_PENALTY_FACTOR = 0.10
    NEWS_BEAR_MAX_HOLDINGS = 2

    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
    FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-in-production")
    PUBLIC_DASHBOARD_URL = os.getenv(
        "PUBLIC_DASHBOARD_URL",
        "http://localhost:1688",
    )


V34_MODE_PRESETS = {
    "aggressive": {
        "v34_revenue_yoy_min": "14.0",
        "v34_breakout_ratio": "0.91",
        "v34_volume_ratio_min": "0.75",
        "v34_volume_min": "250",
        "v34_relaxed_revenue_yoy_min": "6.0",
        "v34_relaxed_breakout_ratio": "0.88",
        "v34_relaxed_volume_ratio_min": "0.55",
        "v34_relaxed_volume_min": "120",
    },
    "balanced": {
        "v34_revenue_yoy_min": "18.0",
        "v34_breakout_ratio": "0.93",
        "v34_volume_ratio_min": "0.90",
        "v34_volume_min": "300",
        "v34_relaxed_revenue_yoy_min": "10.0",
        "v34_relaxed_breakout_ratio": "0.90",
        "v34_relaxed_volume_ratio_min": "0.70",
        "v34_relaxed_volume_min": "150",
    },
    "loose": {
        "v34_revenue_yoy_min": "8.0",
        "v34_breakout_ratio": "0.87",
        "v34_volume_ratio_min": "0.50",
        "v34_volume_min": "120",
        "v34_relaxed_revenue_yoy_min": "0.0",
        "v34_relaxed_breakout_ratio": "0.84",
        "v34_relaxed_volume_ratio_min": "0.30",
        "v34_relaxed_volume_min": "60",
    },
    "conservative": {
        "v34_revenue_yoy_min": "22.0",
        "v34_breakout_ratio": "0.96",
        "v34_volume_ratio_min": "1.00",
        "v34_volume_min": "500",
        "v34_relaxed_revenue_yoy_min": "14.0",
        "v34_relaxed_breakout_ratio": "0.92",
        "v34_relaxed_volume_ratio_min": "0.80",
        "v34_relaxed_volume_min": "300",
    },
}

V35_MODE_PRESETS = {
    "aggressive": {
        "v35_op_margin_min": "0.05",
        "v35_revenue_yoy_min": "-2.0",
        "v35_volume_ratio_min": "0.70",
        "v35_volume_min": "250",
        "v35_relaxed_op_margin_min": "0.03",
        "v35_relaxed_revenue_yoy_min": "-8.0",
        "v35_relaxed_volume_ratio_min": "0.50",
        "v35_relaxed_volume_min": "120",
    },
    "balanced": {
        "v35_op_margin_min": "0.06",
        "v35_revenue_yoy_min": "0.0",
        "v35_volume_ratio_min": "0.80",
        "v35_volume_min": "300",
        "v35_relaxed_op_margin_min": "0.04",
        "v35_relaxed_revenue_yoy_min": "-5.0",
        "v35_relaxed_volume_ratio_min": "0.60",
        "v35_relaxed_volume_min": "150",
    },
    "loose": {
        "v35_op_margin_min": "0.03",
        "v35_revenue_yoy_min": "-10.0",
        "v35_volume_ratio_min": "0.50",
        "v35_volume_min": "120",
        "v35_relaxed_op_margin_min": "0.01",
        "v35_relaxed_revenue_yoy_min": "-20.0",
        "v35_relaxed_volume_ratio_min": "0.30",
        "v35_relaxed_volume_min": "60",
    },
    "conservative": {
        "v35_op_margin_min": "0.08",
        "v35_revenue_yoy_min": "3.0",
        "v35_volume_ratio_min": "0.90",
        "v35_volume_min": "500",
        "v35_relaxed_op_margin_min": "0.05",
        "v35_relaxed_revenue_yoy_min": "0.0",
        "v35_relaxed_volume_ratio_min": "0.70",
        "v35_relaxed_volume_min": "300",
    },
}

MODE_CMD_MAP = {
    "切換積極": ("積極", "aggressive"),
    "積極": ("積極", "aggressive"),
    "切積極": ("積極", "aggressive"),
    "切換平衡": ("平衡", "balanced"),
    "平衡": ("平衡", "balanced"),
    "切平衡": ("平衡", "balanced"),
    "切換寬鬆": ("寬鬆", "loose"),
    "寬鬆": ("寬鬆", "loose"),
    "切寬鬆": ("寬鬆", "loose"),
    "切換穩健": ("穩健", "conservative"),
}

MODE_EMOJI = {
    "aggressive": "😈",
    "balanced": "⚖️",
    "loose": "🌊",
    "conservative": "🛡️",
}

MODE_REPLY_TEMPLATE = {
    "aggressive": "已切換至【積極模式】\nV34/V35 已同步放寬（嚴格與放寬門檻）",
    "balanced": "已切換至【平衡模式】\nV34/V35 已同步套用平衡門檻",
    "loose": "已切換至【寬鬆模式】\nV34/V35 已同步放寬，增加可選股票數",
    "conservative": "已切換至【穩健模式】(相容模式)",
}

USER_SETTINGS_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS user_settings (
    setting_key VARCHAR(50) PRIMARY KEY,
    setting_value VARCHAR(100) NOT NULL,
    description VARCHAR(200),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_updated_at (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
""".strip()

USER_SETTINGS_UPSERT_SQL = """
INSERT INTO user_settings (setting_key, setting_value, description)
VALUES (:key, :value, :desc)
ON DUPLICATE KEY UPDATE
    description = :desc,
    updated_at = CURRENT_TIMESTAMP
""".strip()

DEFAULT_USER_SETTINGS = (
    ("mode", "conservative", "策略模式 (conservative穩健/aggressive積極)"),
    ("ai_threshold", "0.50", "AI 信心門檻（50%）"),
    ("ai_top_n", "5", "AI 推薦數量（前N名）"),
    ("stop_loss", "0.08", "停損點（8%）"),
    ("take_profit", "0.20", "停利點（20%）"),
    ("max_hold_days", "20", "最長持有天數"),
    ("max_holdings", "3", "最大持倉數"),
    ("position_size", "0.33", "單筆倉位比例（33%）"),
    ("volume_filter_conservative", "2000000", "成交量門檻-穩健模式（200萬股）"),
    ("volume_filter_aggressive", "1000000", "成交量門檻-積極模式（100萬股）"),
    ("use_ma20_filter", "true", "是否使用月線過濾（站上MA20）"),
    ("enable_news", "true", "是否啟用新聞推播"),
    ("enable_chips_display", "true", "是否顯示籌碼資訊"),
    ("enable_strategy_report", "true", "是否啟用策略報告"),
    ("notify_threshold", "0.60", "高信心提醒門檻（60%）"),
    ("daily_report_time", "08:00", "每日報告推播時間"),
)

USER_SETTINGS_CATEGORIES = {
    "🎯 策略模式": ("mode",),
    "🤖 AI 參數": ("ai_threshold", "ai_top_n", "notify_threshold"),
    "🛡️ 風控參數": ("stop_loss", "take_profit", "max_hold_days"),
    "💰 倉位管理": ("max_holdings", "position_size"),
    "📊 選股篩選": (
        "volume_filter_conservative",
        "volume_filter_aggressive",
        "use_ma20_filter",
    ),
    "⚙️ 功能開關": (
        "enable_news",
        "enable_chips_display",
        "enable_strategy_report",
    ),
    "🔔 通知設定": ("daily_report_time",),
}


def get_default_user_settings():
    """Return a mutable copy of the default DB-backed user settings."""
    return [tuple(row) for row in DEFAULT_USER_SETTINGS]


def get_user_settings_dict(settings=None):
    """Return default user settings keyed by setting name."""
    source = settings if settings is not None else DEFAULT_USER_SETTINGS
    return {key: (value, description) for key, value, description in source}


__all__ = [
    "Config",
    "DEFAULT_USER_SETTINGS",
    "MODE_CMD_MAP",
    "MODE_EMOJI",
    "MODE_REPLY_TEMPLATE",
    "USER_SETTINGS_CATEGORIES",
    "USER_SETTINGS_CREATE_TABLE_SQL",
    "USER_SETTINGS_UPSERT_SQL",
    "V34_MODE_PRESETS",
    "V35_MODE_PRESETS",
    "get_default_user_settings",
    "get_user_settings_dict",
]