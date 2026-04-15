"""
V37 均值回歸策略 (Mean Reversion Strategy)
============================================
核心理念：
  股價區間震盪時，價格偏離均值後傾向回歸。
  利用 KD 指標金叉 + BB 收斂 + 量縮整理，捕捉超跌後的反彈行情。

篩選邏輯：
  Stage 1 — 基底確認：收盤 > MA60（非崩盤股）+ 基本流動性
  Stage 2 — KD 超賣回升：kd_k < 35（超賣區）或 kd_k 穿越回升中
  Stage 3 — BB 收斂：bb_width < 門檻（波動收斂＝即將變盤）+ bias 接近 0
  Stage 4 — 量縮確認：volume_ratio < 1.0（量能萎縮整理）+ RSI 35~55

出場規則：
  使用 BaseStrategy 預設階梯式尾停（不覆寫 check_exit_signal）

適用場景：
  盤整期的區間操作，持股週期 5-8 天
"""

from typing import List
import pandas as pd
from config import Config
from .base import BaseStrategy


class V37MeanReversionStrategy(BaseStrategy):
    """V37 均值回歸策略 — 區間震盪反轉"""

    # ============================================
    # 必要屬性 (Abstract Properties)
    # ============================================

    @property
    def name(self) -> str:
        return 'v37_mean_reversion'

    @property
    def display_name(self) -> str:
        return 'V37 均值回歸策略'

    @property
    def description(self) -> str:
        return (
            'KD 超賣回升 + BB 收斂 + 量縮整理，'
            '捕捉區間震盪中的均值回歸行情。'
        )

    @property
    def features(self) -> List[str]:
        return [
            'kd_k',          # KD 指標（核心：超賣判斷）
            'bb_width',      # 布林通道寬度（核心：收斂判斷）
            'bias',          # 乖離率（核心：偏離均值程度）
            'rsi',           # RSI（輔助：超賣確認）
            'std_20',        # 20 日標準差（波動度）
            'natr',          # 標準化波動度
            'macd_hist',     # MACD 柱狀體（趨勢方向）
            'volume_ratio',  # 量比（量縮確認）
            'atr',           # ATR（停損參考）
        ]

    @property
    def target_return(self) -> float:
        return 0.05  # 目標報酬 5%（短線反彈）

    @property
    def look_ahead_days(self) -> int:
        return 8  # 向前看 8 天

    # ============================================
    # 可選屬性
    # ============================================

    @property
    def stop_loss(self) -> float:
        return self._get_float_setting('V37_STOP_LOSS', Config.V37_STOP_LOSS)

    @property
    def take_profit(self) -> float:
        return self._get_float_setting('V37_TAKE_PROFIT', Config.V37_TAKE_PROFIT)

    @property
    def max_hold_days(self) -> int:
        return int(self._get_float_setting('V37_MAX_HOLD_DAYS', Config.V37_MAX_HOLD_DAYS))

    # ============================================
    # 核心篩選
    # ============================================

    def filter_candidates(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        V37 均值回歸篩選

        Stage 1: 基底確認 — 非崩盤 + 流動性
        Stage 2: KD 超賣回升 — kd_k < 35
        Stage 3: BB 收斂 + bias 接近均值
        Stage 4: 量縮 + RSI 偏冷

        Args:
            df: 含完整指標的全市場 DataFrame

        Returns:
            篩選後候選股，按 kd_k 升序排列（最超賣優先）
        """
        if df.empty:
            return df

        # 排除非個股（ETF/權證/債券/KY）
        df = self._filter_real_stocks(df)

        # 欄位檢查
        required = ['close_price', 'ma60', 'volume']
        for col in required:
            if col not in df.columns:
                print(f"⚠️ V37 缺少必要欄位: {col}")
                return pd.DataFrame()

        # 日期 & 大盤過濾
        date_str = self._extract_date_str(df)
        if not self._check_market_filter(date_str, 'V37'):
            return pd.DataFrame()

        # 數值清洗
        numeric_cols = [
            'close_price', 'ma20', 'ma60', 'volume', 'volume_ratio',
            'kd_k', 'bb_width', 'bias', 'rsi', 'std_20', 'natr',
            'macd_hist', 'atr',
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        total = len(df)

        # ── Stage 1: 基底確認 ──
        # 非崩盤：收盤仍在 MA60 之上（避免抄底破底股）
        base_mask = df['close_price'] > df['ma60']
        liquidity_mask = df['volume'] > Config.V37_VOLUME_THRESHOLD

        df = df[base_mask & liquidity_mask]
        print(f"  📊 V37 Stage 1 (基底+流動性): {total} → {len(df)}")

        if df.empty:
            return df

        # ── Stage 2: KD 超賣回升 ──
        if 'kd_k' in df.columns:
            before = len(df)
            # KD 低檔區 — 超賣回升判斷
            kd_mask = df['kd_k'] < Config.V37_KD_LOW
            df = df[kd_mask]
            print(f"  📊 V37 Stage 2 (KD 超賣): {before} → {len(df)}  (KD_K < {Config.V37_KD_LOW})")

        if df.empty:
            return df

        # ── Stage 3: BB 收斂 + Bias 接近均值 ──
        if 'bb_width' in df.columns:
            before = len(df)
            df = df[df['bb_width'] < Config.V37_BB_WIDTH_MAX]
            print(f"  📊 V37 Stage 3a (BB 收斂): {before} → {len(df)}  (BB_width < {Config.V37_BB_WIDTH_MAX})")

        if 'bias' in df.columns and not df.empty:
            before = len(df)
            df = df[
                (df['bias'] > Config.V37_BIAS_LOW) &
                (df['bias'] < Config.V37_BIAS_HIGH)
            ]
            print(f"  📊 V37 Stage 3b (Bias 均值): {before} → {len(df)}  ({Config.V37_BIAS_LOW} < bias < {Config.V37_BIAS_HIGH})")

        if df.empty:
            return df

        # ── Stage 4: 量縮 + RSI 偏冷 ──
        if 'volume_ratio' in df.columns:
            before = len(df)
            df = df[df['volume_ratio'] < Config.V37_VOLUME_RATIO_MAX]
            print(f"  📊 V37 Stage 4a (量縮): {before} → {len(df)}  (volume_ratio < {Config.V37_VOLUME_RATIO_MAX})")

        if 'rsi' in df.columns and not df.empty:
            before = len(df)
            df = df[
                (df['rsi'] >= Config.V37_RSI_LOW) &
                (df['rsi'] <= Config.V37_RSI_HIGH)
            ]
            print(f"  📊 V37 Stage 4b (RSI): {before} → {len(df)}  ({Config.V37_RSI_LOW} <= RSI <= {Config.V37_RSI_HIGH})")

        if df.empty:
            return df

        # 排序：KD 最低（最超賣）優先
        if 'kd_k' in df.columns:
            df = df.sort_values('kd_k', ascending=True)

        print(f"  ✅ V37 篩選完成: {len(df)} 檔均值回歸候選股")
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
            'type': '反轉型',
            'risk_level': '中',
            'features': self.features,
            'params': {
                'kd_low': Config.V37_KD_LOW,
                'bb_width_max': Config.V37_BB_WIDTH_MAX,
                'bias_range': f'{Config.V37_BIAS_LOW}~{Config.V37_BIAS_HIGH}',
                'volume_ratio_max': Config.V37_VOLUME_RATIO_MAX,
                'rsi_range': f'{Config.V37_RSI_LOW}~{Config.V37_RSI_HIGH}',
                'stop_loss': self.stop_loss,
                'take_profit': self.take_profit,
                'max_hold_days': self.max_hold_days,
            }
        }
