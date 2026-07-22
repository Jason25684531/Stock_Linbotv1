"""
V35 經營效益策略 (Operating Efficiency Strategy)
============================================
基於基本面的長線投資策略，專注於營業利益率高且營收成長的優質股

🎯 策略特色：
- 核心邏輯：營業利益率 > 10% + 營收正成長 + 技術面多頭
- 中低風險中報酬：追求穩健成長
- 適用場景：全市場、穩健型投資人

📊 預期績效：
- 勝率：~60-70%（高勝率）
- 平均報酬：15-20%
- 最大回撤：~15%（中等回撤）
- 持有期：中長線（30-60 天）

🔍 篩選邏輯：
1. op_profit_margin > 0.06（營業利益率 > 6%）
2. revenue_yoy > 0（營收正成長）
3. close > ma60（多頭排列）
4. volume_ratio > 0.8（流動性足夠）
5. eps > 0（獲利能力）

💡 策略理念：
- 營業利益率是企業經營效率的核心指標
- 結合基本面（財報）+ 技術面（趨勢）
- 避開虧損公司，專注獲利成長股
"""

from typing import List
import pandas as pd
from config import Config
from .base import BaseStrategy


class QualityGrowthStrategy(BaseStrategy):
    """V35 經營效益策略 - 基本面驅動的高利潤率成長股"""
    
    # ============================================
    # 策略屬性定義
    # ============================================
    
    @property
    def name(self) -> str:
        return 'v35_innovation'
    
    @property
    def display_name(self) -> str:
        return 'V35 經營效益策略'
    
    @property
    def description(self) -> str:
        return '營業利益率高 (>6%) + 營收成長 + 多頭趨勢，中長線穩健成長'
    
    @property
    def features(self) -> List[str]:
        """V35 使用的 AI 特徵
        
        強調基本面與穩健性指標
        """
        return [
            # 核心：基本面指標（來自財報）
            'revenue_yoy',           # 營收年增率（核心）
            'op_profit_margin',      # 營業利益率（核心）
            
            # 技術面：趨勢與動能
            'rsi',           # 相對強弱指標
            'bias',          # 乖離率
            'macd_hist',     # MACD 柱狀圖
            
            # 輔助指標
            'volume_ratio',  # 成交量比例
            'kd_k',          # KD 指標
            'bb_width',      # 布林通道寬度（波動度）
            
            # 籌碼面
            'foreign_ratio', # 外資參與度
            'trust_ratio'    # 投信參與度
        ]
    
    @property
    def target_return(self) -> float:
        """目標報酬率 15%（中等積極）"""
        return 0.15
    
    @property
    def look_ahead_days(self) -> int:
        """預測未來 20 天（中長線）"""
        return 20
    
    @property
    def stop_loss(self) -> float:
        """停損 10%（保守）"""
        return 0.10
    
    @property
    def take_profit(self) -> float:
        """停利 20%（合理目標）"""
        return 0.20
    
    @property
    def max_hold_days(self) -> int:
        """最長持有 60 天（中長線）"""
        return 60
    
    # ============================================
    # V35 核心篩選邏輯
    # ============================================

    def filter_candidates(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        V35 篩選邏輯 - 專注於營業利益率
        
        🎯 V35 放寬版本（2026-02）：
        - 營業利益率: 6% (原 10%)
        - 適應市場平均利潤率
        """
        if df.empty:
            return df

        # 排除非個股（ETF/權證/債券/KY）
        df = self._filter_real_stocks(df)

        print(f"\n🔍 [V35] 原始候選股票數：{len(df)}")
        
        # 補齊欄位並清理 None/NaN 值
        required_cols = ['op_profit_margin', 'revenue_yoy', 'eps', 'close_price', 'ma60', 'volume_ratio']
        for col in required_cols:
            if col not in df.columns:
                df[col] = 0.0
            else:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        strict_op = self._get_float_setting('v35_op_margin_min', Config.V35_OP_MARGIN_MIN)
        strict_rev = self._get_float_setting('v35_revenue_yoy_min', Config.V35_REVENUE_YOY_MIN)
        strict_vol = self._get_float_setting('v35_volume_ratio_min', Config.V35_VOLUME_RATIO_MIN)
        strict_vol_min = self._get_float_setting('v35_volume_min', Config.V35_VOLUME_MIN)

        relaxed_op = self._get_float_setting('v35_relaxed_op_margin_min', Config.V35_RELAXED_OP_MARGIN_MIN)
        relaxed_rev = self._get_float_setting('v35_relaxed_revenue_yoy_min', Config.V35_RELAXED_REVENUE_YOY_MIN)
        relaxed_vol = self._get_float_setting('v35_relaxed_volume_ratio_min', Config.V35_RELAXED_VOLUME_RATIO_MIN)
        relaxed_vol_min = self._get_float_setting('v35_relaxed_volume_min', Config.V35_RELAXED_VOLUME_MIN)

        def apply_v35_filters(
            source: pd.DataFrame,
            op_min: float,
            rev_min: float,
            vol_min: float,
            vol_abs_min: float,
            label: str
        ) -> pd.DataFrame:
            result = source.copy()

            result = result[result['op_profit_margin'] > op_min].copy()
            print(f"  ✓ [{label}] 營業利益率 > {op_min*100:.1f}%：{len(result)} 檔")
            if result.empty:
                return result

            result = result[result['revenue_yoy'] > rev_min].copy()
            print(f"  ✓ [{label}] 營收成長 > {rev_min:.1f}%：{len(result)} 檔")
            if result.empty:
                return result

            result = result[result['eps'] > 0].copy()
            print(f"  ✓ [{label}] 有獲利 (EPS>0)：{len(result)} 檔")
            if result.empty:
                return result

            result = result[result['close_price'] > result['ma60']].copy()
            print(f"  ✓ [{label}] 多頭排列 (收盤>MA60)：{len(result)} 檔")
            if result.empty:
                return result

            ratio_available = 'volume_ratio' in result.columns and (result['volume_ratio'] > 0).any()
            if ratio_available:
                result = result[result['volume_ratio'] > vol_min].copy()
                print(f"  ✓ [{label}] 流動性足夠 (量比>{vol_min:.2f})：{len(result)} 檔")
            else:
                result = result[result['volume'] >= vol_abs_min].copy()
                print(f"  ✓ [{label}] 流動性備援 (成交量>={vol_abs_min:.0f})：{len(result)} 檔")

            return result

        df_final = apply_v35_filters(df, strict_op, strict_rev, strict_vol, strict_vol_min, '嚴格')

        if df_final.empty:
            print("  ⚠️ 嚴格條件無候選，啟用 V35 放寬參數重試")
            df_final = apply_v35_filters(df, relaxed_op, relaxed_rev, relaxed_vol, relaxed_vol_min, '放寬')

        print(f"\n✅ V35 篩選完成：{len(df_final)} 檔高效益成長股")

        return df_final
    
    # ============================================
    # 輔助方法
    # ============================================
    
    def get_recommendation_message(self, stock_id: str, stock_data: pd.Series) -> str:
        """
        生成推薦訊息（用於 LINE 通知）
        
        Args:
            stock_id: 股票代號
            stock_data: 股票資料 (Series)
        
        Returns:
            格式化的推薦訊息
        """
        op_margin = stock_data.get('op_profit_margin', 0) * 100
        revenue_yoy = stock_data.get('revenue_yoy', 0)
        eps = stock_data.get('eps', 0)
        quality_score = stock_data.get('v35_quality_score', 0) * 100
        
        msg = (
            f"💼 經營效益股 ({stock_id})\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📊 基本面：\n"
            f"• 營業利益率：{op_margin:.2f}%\n"
            f"• 營收成長：{revenue_yoy:+.1f}%\n"
            f"• 每股盈餘：{eps:.2f} 元\n\n"
            f"⭐ 品質評分：{quality_score:.1f}/100\n\n"
            f"💡 策略：V35 中長線（持有 30-60 天）\n"
            f"停損 10% | 目標 20%"
        )
        return msg
    
    def validate_data_quality(self, df: pd.DataFrame) -> dict:
        """
        驗證資料品質
        
        Args:
            df: 輸入資料框
        
        Returns:
            驗證報告 (dict)
        """
        report = {
            'total_rows': len(df),
            'has_op_margin_data': 0,
            'has_revenue_data': 0,
            'has_eps_data': 0,
            'complete_data': 0
        }
        
        if df.empty:
            return report
        
        # 統計資料完整性
        if 'op_profit_margin' in df.columns:
            report['has_op_margin_data'] = int((df['op_profit_margin'] > 0).sum())
        
        if 'revenue_yoy' in df.columns:
            report['has_revenue_data'] = int(df['revenue_yoy'].notna().sum())
        
        if 'eps' in df.columns:
            report['has_eps_data'] = int(df['eps'].notna().sum())
        
        # 完整資料（三者皆有）
        if all(col in df.columns for col in ['op_profit_margin', 'revenue_yoy', 'eps']):
            complete_mask = (
                (df['op_profit_margin'] > 0) & 
                df['revenue_yoy'].notna() & 
                df['eps'].notna()
            )
            report['complete_data'] = int(complete_mask.sum())
        
        return report
