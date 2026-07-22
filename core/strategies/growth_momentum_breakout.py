"""
V34 雙渦輪飆股策略 (Twin-Turbo Strategy)
============================================
專注於高營收成長 + 價格突破的爆發股

🎯 策略特色：
- 核心邏輯：營收 YoY > 18% + 接近 60 日新高
- 高風險高報酬：追求月報酬 10% 以上
- 適用場景：多頭市場、積極型投資人

📊 預期績效：
- 勝率：~50%（中等勝率，高報酬）
- 平均報酬：10-15%
- 最大回撤：~20%（較高回撤）
- 持有期：短線（5-7 天）

🔍 篩選邏輯：
1. revenue_yoy > 18%（高成長核心）
2. 收盤價 >= 60日高點 * 0.93（接近突破）
3. volume_ratio > 0.9（有量配合）
4. 收盤 > MA20（短線多頭）
"""

from typing import List
import pandas as pd
from config import Config
from .base import BaseStrategy


class GrowthMomentumBreakoutStrategy(BaseStrategy):
    """V34 雙渦輪飆股策略 - 追求高報酬、短線爆發"""
    
    # ============================================
    # 策略屬性定義
    # ============================================
    
    @property
    def name(self) -> str:
        return 'v34_turbo'
    
    @property
    def display_name(self) -> str:
        return 'V34 雙渦輪飆股策略'
    
    @property
    def description(self) -> str:
        return '高營收成長 (YoY>18%) + 價格突破 (近60日高)，追求短線爆發'
    
    @property
    def features(self) -> List[str]:
        """V34 使用的 AI 特徵
        
        強調成長性與動能指標
        """
        return [
            # 核心：營收成長
            'revenue_yoy',   # 營收年增率（核心）
            
            # 動能指標
            'volume_ratio',  # 成交量比例（核心）
            'rsi',           # 相對強弱指標
            'bias',          # 乖離率（價格偏離度）
            'macd_hist',     # MACD 柱狀圖
            
            # 輔助指標
            'kd_k',          # KD 指標
            'foreign_ratio', # 外資參與度
            'trust_ratio'    # 投信參與度
        ]
    
    @property
    def target_return(self) -> float:
        """目標報酬率 10%（積極）"""
        return 0.10
    
    @property
    def look_ahead_days(self) -> int:
        """預測未來 5 天（短線）"""
        return 5
    
    @property
    def stop_loss(self) -> float:
        """停損 10%（較寬）"""
        return 0.10
    
    @property
    def take_profit(self) -> float:
        """停利 20%（高目標）"""
        return 0.20
    
    @property
    def max_hold_days(self) -> int:
        """最長持有 7 天（短線）"""
        return 7
    
    # ============================================
    # V34 核心篩選邏輯
    # ============================================

    def filter_candidates(self, df: pd.DataFrame) -> pd.DataFrame:
        """V34 雙渦輪篩選邏輯
        
        篩選條件：
        1. revenue_yoy > 18%（高成長核心）
        2. 收盤價 >= 60日高點 * 0.93（接近突破）
        3. volume_ratio > 0.9（量能放大）
        4. 收盤 > MA20（短線多頭）
        
        Args:
            df: DataFrame，包含股票資料與技術指標
        
        Returns:
            DataFrame: 符合條件的候選股票（依 revenue_yoy 降序排列）
        """
        if df.empty:
            return pd.DataFrame()

        # 排除非個股（ETF/權證/債券/KY）
        df = self._filter_real_stocks(df)

        # ============================================
        # 欄位檢查與優雅降級
        # ============================================
        required_cols = ['close_price', 'high_price', 'ma20', 'volume']
        optional_cols = ['revenue_yoy']
        
        # 檢查必要欄位
        for col in required_cols:
            if col not in df.columns:
                print(f"⚠️ V34: 缺少必要欄位 '{col}'，無法篩選")
                return pd.DataFrame()
        
        # 檢查可選欄位
        missing_optional = [col for col in optional_cols if col not in df.columns]
        if missing_optional:
            print(f"⚠️ V34: 缺少指標欄位 {missing_optional}，策略無法運作")
            print(f"   💡 提示：請確保資料庫包含營收資料（revenue_yoy）")
            return pd.DataFrame()
        
        # ============================================
        # 取得日期
        # ============================================
        date_str = self._extract_date_str(df)
        
        print(f"\n🚀 V34 雙渦輪策略篩選 ({date_str})")
        print(f"   目標：高成長 + 價格突破")
        
        # ============================================
        # 市場熔斷檢查（V34 只在多頭市場運作）
        # ============================================
        if not self._check_market_filter(date_str, 'V34'):
            return pd.DataFrame()
        
        # ============================================
        # 資料清理：處理 None/NaN 值
        # ============================================
        numeric_cols = ['close_price', 'high_price', 'ma20', 'volume', 'volume_ratio', 'revenue_yoy']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        
        # ============================================
        # V34 核心篩選
        # ============================================
        
        # 1. 計算 60 日高點
        print(f"   📊 計算 60 日高點...")
        df_sorted = df.sort_values(['stock_id', 'trade_date'])
        df_sorted['high_60'] = df_sorted.groupby('stock_id')['high_price'].transform(
            lambda x: x.rolling(60, min_periods=1).max()
        )
        df_sorted['high_60'] = df_sorted['high_60'].fillna(df_sorted['high_price'])
        
        # 取最新資料
        latest_date = df_sorted['trade_date'].max()
        candidates = df_sorted[df_sorted['trade_date'] == latest_date].copy()
        
        strict_yoy = self._get_float_setting('v34_revenue_yoy_min', Config.V34_REVENUE_YOY_MIN)
        strict_breakout = self._get_float_setting('v34_breakout_ratio', Config.V34_BREAKOUT_RATIO)
        strict_vol = self._get_float_setting('v34_volume_ratio_min', Config.V34_VOLUME_RATIO_MIN)
        strict_vol_min = self._get_float_setting('v34_volume_min', Config.V34_VOLUME_MIN)

        relaxed_yoy = self._get_float_setting('v34_relaxed_revenue_yoy_min', Config.V34_RELAXED_REVENUE_YOY_MIN)
        relaxed_breakout = self._get_float_setting('v34_relaxed_breakout_ratio', Config.V34_RELAXED_BREAKOUT_RATIO)
        relaxed_vol = self._get_float_setting('v34_relaxed_volume_ratio_min', Config.V34_RELAXED_VOLUME_RATIO_MIN)
        relaxed_vol_min = self._get_float_setting('v34_relaxed_volume_min', Config.V34_RELAXED_VOLUME_MIN)

        base_candidates = candidates.copy()

        def apply_v34_filters(
            source: pd.DataFrame,
            yoy_min: float,
            breakout_ratio: float,
            vol_min: float,
            vol_abs_min: float,
            label: str
        ) -> pd.DataFrame:
            result = source.copy()

            if 'revenue_yoy' in result.columns:
                before_count = len(result)
                result = result[result['revenue_yoy'] > yoy_min].copy()
                print(f"   ✅ [{label}] 營收篩選：{before_count} → {len(result)} 檔（YoY > {yoy_min:.1f}%）")
                if result.empty:
                    return result

            if 'high_60' in result.columns:
                before_count = len(result)
                result = result[result['close_price'] >= result['high_60'] * breakout_ratio].copy()
                print(f"   ✅ [{label}] 突破篩選：{before_count} → {len(result)} 檔（收盤 >= 60日高*{breakout_ratio:.2f}）")
                if result.empty:
                    return result

            ratio_available = 'volume_ratio' in result.columns and (result['volume_ratio'] > 0).any()
            if ratio_available:
                before_count = len(result)
                result = result[result['volume_ratio'] > vol_min].copy()
                print(f"   ✅ [{label}] 量能篩選：{before_count} → {len(result)} 檔（量比 > {vol_min:.2f}）")
                if result.empty:
                    return result
            else:
                before_count = len(result)
                result = result[result['volume'] >= vol_abs_min].copy()
                print(f"   ✅ [{label}] 量能備援：{before_count} → {len(result)} 檔（成交量 >= {vol_abs_min:.0f}）")
                if result.empty:
                    return result

            before_count = len(result)
            result = result[result['close_price'] > result['ma20']].copy()
            print(f"   ✅ [{label}] 趨勢篩選：{before_count} → {len(result)} 檔（收盤 > MA20）")

            return result

        candidates = apply_v34_filters(
            base_candidates,
            strict_yoy,
            strict_breakout,
            strict_vol,
            strict_vol_min,
            '嚴格'
        )

        if candidates.empty:
            print("   ⚠️ 嚴格條件無候選，啟用 V34 放寬參數重試")
            candidates = apply_v34_filters(
                base_candidates,
                relaxed_yoy,
                relaxed_breakout,
                relaxed_vol,
                relaxed_vol_min,
                '放寬'
            )

        if candidates.empty:
            print(f"   ❌ V34 放寬後仍無符合條件股票")
            return pd.DataFrame()
        
        # ============================================
        # 排序：依營收成長率由高到低
        # ============================================
        if 'revenue_yoy' in candidates.columns:
            candidates = candidates.sort_values('revenue_yoy', ascending=False)
            print(f"   📊 依 revenue_yoy 降序排列（優先選成長最快的）")
        
        print(f"\n✅ V34 篩選完成：{len(candidates)} 檔高成長突破股票")
        
        # 清理暫存欄位
        if 'high_60' in candidates.columns:
            candidates = candidates.drop(columns=['high_60'])
        
        return candidates
    
    # ============================================
    # 額外方法
    # ============================================
    
    def get_strategy_info(self) -> dict:
        """取得策略資訊
        
        Returns:
            dict: 策略詳細資訊
        """
        return {
            'name': self.name,
            'display_name': self.display_name,
            'type': '積極型',
            'risk_level': '高',
            'target_return': f'{self.target_return*100}%',
            'max_drawdown_target': '20%',
            'win_rate_target': '50%',
            'holding_period': f'{self.max_hold_days} 天',
            'core_logic': 'revenue_yoy > 18% + 接近60日高 + 量能放大',
            'suitable_for': '積極型投資人、多頭市場'
        }
