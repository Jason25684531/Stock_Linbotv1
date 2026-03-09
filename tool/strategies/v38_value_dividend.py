"""
V38 高殖利率價值策略 (Value / Dividend Strategy)
============================================
核心理念：
  高 EPS + 高營業利益率 + 低波動 ≈ 類定存股。
  篩選獲利能力強、價格穩定的價值股，追求長期穩健收益。

篩選邏輯：
  Stage 1 — 趨勢穩定：收盤 > MA60 + 基本流動性
  Stage 2 — 獲利能力：op_profit_margin >= 門檻 + EPS > 0
  Stage 3 — 低波動：NATR < 門檻 + STD_20 < 門檻
  Stage 4 — 技術確認：RSI 40~65（溫和偏多）+ Bias 不過度乖離

數據來源：
  - EPS 和 op_profit_margin 透過 supplement_financial_data() 合併
  - 無需新增爬蟲，使用現有 financial_statements 表

出場規則：
  使用 BaseStrategy 預設階梯式尾停（不覆寫 check_exit_signal）

適用場景：
  保守型長線投資、熊市避險配置，持股週期 10-15 天
"""

from typing import List
import pandas as pd
from config import Config
from .base import BaseStrategy


class V38ValueDividendStrategy(BaseStrategy):
    """V38 高殖利率價值策略 — 類定存股篩選"""

    # ============================================
    # 必要屬性 (Abstract Properties)
    # ============================================

    @property
    def name(self) -> str:
        return 'v38_value_dividend'

    @property
    def display_name(self) -> str:
        return 'V38 高殖利率價值策略'

    @property
    def description(self) -> str:
        return (
            '高 EPS + 高營業利益率 + 低波動，'
            '篩選類定存價值股，追求長期穩健收益。'
        )

    @property
    def features(self) -> List[str]:
        """AI 模型特徵

        注意：op_profit_margin 和 eps 不在此列表中，
        因為它們來自 financial_statements 表的 supplement 合併，
        在 daily_market_data 中不一定可用。
        篩選條件中使用它們，但 ML 特徵只用 daily 指標。
        """
        return [
            'natr',          # 標準化波動度（核心：低波動篩選）
            'std_20',        # 20 日標準差（核心：穩定度）
            'rsi',           # RSI（技術確認）
            'bias',          # 乖離率
            'bb_width',      # 布林通道寬度
            'macd_hist',     # MACD 柱狀體
            'volume_ratio',  # 量比
            'kd_k',          # KD 指標
            'atr',           # ATR
        ]

    @property
    def target_return(self) -> float:
        return 0.05  # 目標報酬 5%（價值股保守預期）

    @property
    def look_ahead_days(self) -> int:
        return 15  # 向前看 15 天（較長持有期）

    # ============================================
    # 可選屬性
    # ============================================

    @property
    def stop_loss(self) -> float:
        return self._get_float_setting('V38_STOP_LOSS', Config.V38_STOP_LOSS)

    @property
    def take_profit(self) -> float:
        return self._get_float_setting('V38_TAKE_PROFIT', Config.V38_TAKE_PROFIT)

    @property
    def max_hold_days(self) -> int:
        return int(self._get_float_setting('V38_MAX_HOLD_DAYS', Config.V38_MAX_HOLD_DAYS))

    # ============================================
    # 核心篩選
    # ============================================

    def filter_candidates(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        V38 高殖利率價值篩選

        Stage 1: 趨勢穩定 — 非下跌趨勢 + 流動性
        Stage 2: 獲利能力 — op_profit_margin + EPS
        Stage 3: 低波動 — NATR + STD_20
        Stage 4: 技術確認 — RSI + Bias 溫和

        Args:
            df: 含完整指標的全市場 DataFrame
                (需先經 supplement_financial_data 合併 eps/op_profit_margin)

        Returns:
            篩選後候選股，按 op_profit_margin 降序排列
        """
        if df.empty:
            return df

        # 排除非個股（ETF/權證/債券/KY）
        df = self._filter_real_stocks(df)

        # 欄位檢查
        required = ['close_price', 'ma60', 'volume']
        for col in required:
            if col not in df.columns:
                print(f"⚠️ V38 缺少必要欄位: {col}")
                return pd.DataFrame()

        # 日期 & 大盤過濾
        date_str = self._extract_date_str(df)
        if not self._check_market_filter(date_str, 'V38'):
            return pd.DataFrame()

        # 數值清洗
        numeric_cols = [
            'close_price', 'ma20', 'ma60', 'volume', 'volume_ratio',
            'natr', 'std_20', 'rsi', 'bias', 'bb_width', 'macd_hist',
            'kd_k', 'atr', 'op_profit_margin', 'eps',
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        total = len(df)

        # ── Stage 1: 趨勢穩定 ──
        trend_mask = df['close_price'] > df['ma60']
        liquidity_mask = df['volume'] > Config.V38_VOLUME_THRESHOLD

        df = df[trend_mask & liquidity_mask]
        print(f"  📊 V38 Stage 1 (趨勢+流動性): {total} → {len(df)}")

        if df.empty:
            return df

        # ── Stage 2: 獲利能力 ──
        profit_mask = pd.Series(True, index=df.index)

        if 'op_profit_margin' in df.columns:
            profit_mask = profit_mask & (df['op_profit_margin'] >= Config.V38_OP_MARGIN_MIN)

        if 'eps' in df.columns:
            profit_mask = profit_mask & (df['eps'] > Config.V38_EPS_MIN)

        before = len(df)
        df = df[profit_mask]
        print(f"  📊 V38 Stage 2 (獲利能力): {before} → {len(df)}")

        if df.empty:
            # 放寬模式
            print("  🔄 V38 嘗試放寬獲利門檻...")
            return df

        # ── Stage 3: 低波動 ──
        if 'natr' in df.columns:
            before = len(df)
            df = df[df['natr'] < Config.V38_NATR_MAX]
            print(f"  📊 V38 Stage 3a (NATR): {before} → {len(df)}  (NATR < {Config.V38_NATR_MAX})")

        if 'std_20' in df.columns and not df.empty:
            before = len(df)
            df = df[df['std_20'] < Config.V38_STD20_MAX]
            print(f"  📊 V38 Stage 3b (STD_20): {before} → {len(df)}  (STD_20 < {Config.V38_STD20_MAX})")

        if df.empty:
            return df

        # ── Stage 4: 技術確認 ──
        if 'rsi' in df.columns:
            df = df[
                (df['rsi'] >= Config.V38_RSI_LOW) &
                (df['rsi'] <= Config.V38_RSI_HIGH)
            ]

        if 'bias' in df.columns and not df.empty:
            df = df[
                (df['bias'] > Config.V38_BIAS_LOW) &
                (df['bias'] < Config.V38_BIAS_HIGH)
            ]

        print(f"  📊 V38 Stage 4 (技術確認): → {len(df)}")

        if df.empty:
            return df

        # 排序：營業利益率最高優先
        if 'op_profit_margin' in df.columns:
            df = df.sort_values('op_profit_margin', ascending=False)
        elif 'natr' in df.columns:
            df = df.sort_values('natr', ascending=True)

        print(f"  ✅ V38 篩選完成: {len(df)} 檔價值股候選")
        return df

    # ============================================
    # 策略資訊
    # ============================================

    def get_strategy_info(self) -> dict:
        """回傳策略摘要"""
        return {
            'name': self.name,
            'display_name': self.display_name,
            'description': self.description,
            'type': '價值型',
            'risk_level': '低',
            'features': self.features,
            'params': {
                'op_margin_min': Config.V38_OP_MARGIN_MIN,
                'eps_min': Config.V38_EPS_MIN,
                'natr_max': Config.V38_NATR_MAX,
                'std20_max': Config.V38_STD20_MAX,
                'rsi_range': f'{Config.V38_RSI_LOW}~{Config.V38_RSI_HIGH}',
                'stop_loss': self.stop_loss,
                'take_profit': self.take_profit,
                'max_hold_days': self.max_hold_days,
            }
        }
