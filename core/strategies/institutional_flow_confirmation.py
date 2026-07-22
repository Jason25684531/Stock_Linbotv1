"""
V36 籌碼動能策略 (Chip Momentum Strategy)
============================================
核心理念：
  三大法人（外資 + 投信 + 自營商）連續買超 + 融資減少 = 主力佈局訊號。
  結合 chip_score 綜合分數與技術面趨勢確認，篩選法人青睞且散戶退場的個股。

篩選邏輯：
  Stage 1 — 趨勢確認：收盤 > MA20 > MA60（多頭排列）
  Stage 2 — 籌碼強度：chip_score >= 門檻 + 外資或投信連買 >= N 天
  Stage 3 — 量能確認：volume_ratio >= 門檻
  Stage 4 — 技術過濾：RSI 45~80（排除超賣但允許強勢）, bias < 15%

出場規則：
  - 繼承 BaseStrategy 的階梯式尾停 + ATR 停損
  - chip_score 跌破 30 加速出場（覆寫 check_exit_signal）

適用場景：
  法人主導的中型股行情，持股週期 7-15 天
"""

from typing import List, Tuple
import pandas as pd
from config import Config
from .base import BaseStrategy


class InstitutionalFlowConfirmationStrategy(BaseStrategy):
    """V36 籌碼動能策略"""

    # ============================================
    # 必要屬性 (Abstract Properties)
    # ============================================

    @property
    def name(self) -> str:
        return 'v36_chip_momentum'

    @property
    def display_name(self) -> str:
        return 'V36 籌碼動能策略'

    @property
    def description(self) -> str:
        return (
            '三大法人連買 + chip_score 綜合分數篩選，'
            '捕捉主力佈局中的趨勢啟動股。'
        )

    @property
    def features(self) -> List[str]:
        return [
            'chip_score',           # 籌碼綜合分數 (0~100)
            'foreign_consec_days',  # 外資連買天數
            'trust_consec_days',    # 投信連買天數
            'foreign_ratio',        # 外資買超佔成交量
            'trust_ratio',          # 投信買超佔成交量
            'dealer_ratio',         # 自營商買超佔成交量
            'margin_change_pct',    # 融資餘額日變動率
            'volume_ratio',         # 量比
            'rsi',                  # 相對強弱指標
            'bias',                 # 乖離率
            'macd_hist',            # MACD 柱狀體
        ]

    @property
    def target_return(self) -> float:
        return 0.07  # 目標報酬 7%

    @property
    def look_ahead_days(self) -> int:
        return 10  # 向前看 10 天

    # ============================================
    # 可選屬性 (覆寫預設值)
    # ============================================

    @property
    def stop_loss(self) -> float:
        return self._get_float_setting('V36_STOP_LOSS', Config.V36_STOP_LOSS)

    @property
    def take_profit(self) -> float:
        return self._get_float_setting('V36_TAKE_PROFIT', Config.V36_TAKE_PROFIT)

    @property
    def max_hold_days(self) -> int:
        return int(self._get_float_setting('V36_MAX_HOLD_DAYS', Config.V36_MAX_HOLD_DAYS))

    # ============================================
    # 核心篩選
    # ============================================

    def filter_candidates(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        V36 籌碼動能篩選

        Stage 1: 趨勢確認 — 多頭排列 + 基本流動性
        Stage 2: 籌碼強度 — chip_score + 法人連買天數
        Stage 3: 量能確認 — volume_ratio
        Stage 4: 技術過濾 — RSI + bias 排除極端

        Args:
            df: 含完整指標的全市場 DataFrame

        Returns:
            篩選後的候選股 DataFrame，按 chip_score 降序排列
        """
        if df.empty:
            return df

        # 欄位檢查
        required = ['close_price', 'ma60', 'volume']
        for col in required:
            if col not in df.columns:
                print(f"⚠️ V36 缺少必要欄位: {col}")
                return pd.DataFrame()

        # 只保留正規上市櫃個股
        df = self._filter_real_stocks(df)

        # 提取日期 & 大盤過濾
        date_str = self._extract_date_str(df)
        if not self._check_market_filter(date_str, 'V36'):
            return pd.DataFrame()

        # 數值清洗
        numeric_cols = [
            'close_price', 'ma20', 'ma60', 'volume', 'volume_ratio',
            'chip_score', 'foreign_consec_days', 'trust_consec_days',
            'foreign_ratio', 'trust_ratio', 'dealer_ratio',
            'margin_change_pct', 'rsi', 'bias', 'macd_hist',
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        total = len(df)

        # ── Stage 1: 趨勢確認 ──
        trend_mask = df['close_price'] > df['ma60']
        if 'ma20' in df.columns:
            trend_mask = trend_mask & (df['close_price'] > df['ma20'])
            trend_mask = trend_mask & (df['ma20'] > df['ma60'])

        # 基本流動性
        liquidity_mask = df['volume'] > Config.V36_VOLUME_THRESHOLD

        df = df[trend_mask & liquidity_mask]
        print(f"  📊 V36 Stage 1 (趨勢+流動性): {total} → {len(df)}")

        if df.empty:
            return df

        # 保存 Stage 1 結果，供放寬模式使用
        df_stage1 = df.copy()

        # ── Stage 2: 籌碼強度 ──
        chip_mask = pd.Series(True, index=df.index)

        if 'chip_score' in df.columns:
            chip_mask = chip_mask & (df['chip_score'] >= Config.V36_CHIP_SCORE_MIN)

        # 外資或投信需有連續買超
        if 'foreign_consec_days' in df.columns and 'trust_consec_days' in df.columns:
            consec_mask = (
                (df['foreign_consec_days'] >= Config.V36_FOREIGN_CONSEC_MIN) |
                (df['trust_consec_days'] >= Config.V36_TRUST_CONSEC_MIN)
            )
            chip_mask = chip_mask & consec_mask

        before_chip = len(df)
        df = df[chip_mask]
        print(f"  📊 V36 Stage 2 (籌碼強度): {before_chip} → {len(df)}")

        if df.empty:
            # 放寬模式：從 Stage 1 結果降低 chip_score 門檻 50%
            print("  🔄 V36 嘗試放寬模式...")
            relaxed_mask = pd.Series(True, index=df_stage1.index)
            if 'chip_score' in df_stage1.columns:
                relaxed_threshold = Config.V36_CHIP_SCORE_MIN * 0.5
                relaxed_mask = df_stage1['chip_score'] >= relaxed_threshold
            df = df_stage1[relaxed_mask]
            print(f"  📊 V36 放寬結果: {len(df_stage1)} → {len(df)}")
            if df.empty:
                return df

        # ── Stage 3: 量能確認 ──
        if 'volume_ratio' in df.columns:
            before_vol = len(df)
            df = df[df['volume_ratio'] >= Config.V36_VOLUME_RATIO_MIN]
            print(f"  📊 V36 Stage 3 (量能確認): {before_vol} → {len(df)}")

        if df.empty:
            return df

        # ── Stage 4: 技術過濾 ──
        if 'rsi' in df.columns:
            df = df[(df['rsi'] >= Config.V36_RSI_LOW) & (df['rsi'] <= Config.V36_RSI_HIGH)]

        if 'bias' in df.columns:
            df = df[df['bias'] < Config.V36_BIAS_HIGH]

        print(f"  📊 V36 Stage 4 (技術過濾): → {len(df)}")

        # 排序：chip_score 高者優先
        if 'chip_score' in df.columns:
            df = df.sort_values('chip_score', ascending=False)
        elif 'foreign_consec_days' in df.columns:
            df = df.sort_values('foreign_consec_days', ascending=False)

        return df

    # ============================================
    # 覆寫出場訊號：加入 chip_score 衰減加速出場
    # ============================================

    def check_exit_signal(
        self,
        stock_id: str,
        current_price: float,
        current_date,
        position_info: dict,
        market_trend: str = 'BULL',
    ) -> Tuple[str, str, float]:
        """
        V36 加強版出場邏輯

        在 BaseStrategy 的階梯式尾停基礎上，新增：
        - chip_score 跌破 30 → 加速出場（籌碼轉弱信號）
        - 外資翻賣超 → 提高警戒

        Args:
            stock_id: 股票代號
            current_price: 當前價格
            current_date: 當前日期
            position_info: 持倉資訊 dict
            market_trend: 大盤趨勢

        Returns:
            Tuple[action, reason, updated_stop_loss]
        """
        # 先檢查 chip_score 加速出場
        chip_score = position_info.get('chip_score', 50)
        cost = position_info.get('cost', current_price)
        return_pct = (current_price - cost) / cost if cost > 0 else 0

        # 籌碼轉弱 + 獲利回吐 → 加速出場
        if chip_score < 30 and return_pct > 0.02:
            return ('SELL', f'籌碼轉弱 (chip_score={chip_score:.0f}, 獲利{return_pct:.1%})',
                    position_info.get('stop_loss', cost * 0.93))

        # 籌碼崩潰 + 虧損 → 立即止損
        if chip_score < 20 and return_pct < 0:
            return ('SELL', f'籌碼崩潰止損 (chip_score={chip_score:.0f})',
                    position_info.get('stop_loss', cost * 0.93))

        # 其餘交給基類的階梯式尾停
        return super().check_exit_signal(
            stock_id, current_price, current_date, position_info, market_trend
        )

    # ============================================
    # 策略資訊
    # ============================================

    def get_strategy_info(self) -> dict:
        """回傳策略摘要（供 Web Dashboard 顯示）"""
        return {
            'name': self.name,
            'display_name': self.display_name,
            'description': self.description,
            'features': self.features,
            'params': {
                'chip_score_min': Config.V36_CHIP_SCORE_MIN,
                'foreign_consec_min': Config.V36_FOREIGN_CONSEC_MIN,
                'trust_consec_min': Config.V36_TRUST_CONSEC_MIN,
                'volume_ratio_min': Config.V36_VOLUME_RATIO_MIN,
                'rsi_range': f'{Config.V36_RSI_LOW}~{Config.V36_RSI_HIGH}',
                'stop_loss': self.stop_loss,
                'take_profit': self.take_profit,
                'max_hold_days': self.max_hold_days,
            }
        }
