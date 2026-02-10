"""
V35 研發動能策略 (Innovation Momentum Strategy)
============================================
基於基本面的長線投資策略，專注於研發投入高且營收成長的科技股

🎯 策略特色：
- 核心邏輯：研發費用佔比 > 3% + 營收正成長 + 技術面多頭
- 中低風險中報酬：追求穩健成長
- 適用場景：全市場、穩健型投資人

📊 預期績效：
- 勝率：~60-70%（高勝率）
- 平均報酬：15-20%
- 最大回撤：~15%（中等回撤）
- 持有期：中長線（30-60 天）

🔍 篩選邏輯：
1. rd_ratio > 0.03（研發投入 > 3%）
2. revenue_yoy > 0（營收正成長）
3. close > ma60（多頭排列）
4. volume_ratio > 0.8（流動性足夠）
5. eps > 0（獲利能力）

💡 策略理念：
- 研發投入是未來成長的領先指標
- 結合基本面（財報）+ 技術面（趨勢）
- 避開虧損公司，專注獲利成長股
"""

from typing import List
import pandas as pd
from .base import BaseStrategy


class V35InnovationStrategy(BaseStrategy):
    """V35 研發動能策略 - 基本面驅動的科技成長股"""
    
    # ============================================
    # 策略屬性定義
    # ============================================
    
    @property
    def name(self) -> str:
        return 'v35_innovation'
    
    @property
    def display_name(self) -> str:
        return 'V35 研發動能策略'
    
    @property
    def description(self) -> str:
        return '研發投入高 (>3%) + 營收成長 + 多頭趨勢，中長線穩健成長'
    
    @property
    def features(self) -> List[str]:
        """V35 使用的 AI 特徵
        
        強調基本面與穩健性指標
        """
        return [
            # 核心：基本面指標（來自財報）
            'revenue_yoy',   # 營收年增率（核心）
            'rd_ratio',      # 研發費用佔比（核心）
            
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
        V35 篩選邏輯 - 自動適應無研發費用的資料
        """
        if df.empty:
            return df
        
        print(f"\n🔍 [V35] 原始候選股票數：{len(df)}")
        
        # 補齊欄位並清理 None/NaN 值
        required_cols = ['rd_ratio', 'revenue_yoy', 'eps', 'close_price', 'ma60', 'volume_ratio']
        for col in required_cols:
            if col not in df.columns:
                df[col] = 0.0
            else:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        # === 自動切換模式 ===
        # 如果全市場研發費用都是 0 (代表 CSV 沒這欄)，則跳過研發篩選
        if df['rd_ratio'].max() == 0:
            print("  ⚠️ 偵測到無研發數據 (CSV 簡表模式)")
            print("  👉 自動降級策略：跳過研發條件，專注 [營收成長] + [EPS]")
            df_rd = df.copy() # 不過濾
        else:
            # 正常模式：研發 > 3%
            mask_rd = df['rd_ratio'] > 0.03
            df_rd = df[mask_rd].copy()
            print(f"  ✓ 研發投入 > 3%：{len(df_rd)} 檔")
        
        if df_rd.empty: return pd.DataFrame()
        
        # 篩選 2：營收成長
        mask_rev = df_rd['revenue_yoy'] > 0
        df_rev = df_rd[mask_rev].copy()
        print(f"  ✓ 營收正成長：{len(df_rev)} 檔")
        
        if df_rev.empty: return pd.DataFrame()

        # 篩選 3：有獲利 (EPS > 0)
        mask_eps = df_rev['eps'] > 0
        df_eps = df_rev[mask_eps].copy()
        print(f"  ✓ 有獲利 (EPS>0)：{len(df_eps)} 檔")
        
        # 篩選 4：多頭排列
        mask_ma = df_eps['close_price'] > df_eps['ma60']  # 修正：使用 close_price
        df_ma = df_eps[mask_ma].copy()
        print(f"  ✓ 多頭排列 (收盤>MA60)：{len(df_ma)} 檔")
        
        # 篩選 5：成交量
        mask_vol = df_ma['volume_ratio'] > 0.8
        df_final = df_ma[mask_vol].copy()
        print(f"  ✓ 流動性足夠：{len(df_final)} 檔")
        
        print(f"\n✅ V35 篩選完成：{len(df_final)} 檔研發動能股")
        
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
        rd_ratio = stock_data.get('rd_ratio', 0) * 100
        revenue_yoy = stock_data.get('revenue_yoy', 0)
        eps = stock_data.get('eps', 0)
        quality_score = stock_data.get('v35_quality_score', 0) * 100
        
        msg = (
            f"🧪 研發動能股 ({stock_id})\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📊 基本面：\n"
            f"• 研發佔比：{rd_ratio:.2f}%\n"
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
            'has_rd_data': 0,
            'has_revenue_data': 0,
            'has_eps_data': 0,
            'complete_data': 0
        }
        
        if df.empty:
            return report
        
        # 統計資料完整性
        if 'rd_ratio' in df.columns:
            report['has_rd_data'] = int((df['rd_ratio'] > 0).sum())
        
        if 'revenue_yoy' in df.columns:
            report['has_revenue_data'] = int(df['revenue_yoy'].notna().sum())
        
        if 'eps' in df.columns:
            report['has_eps_data'] = int(df['eps'].notna().sum())
        
        # 完整資料（三者皆有）
        if all(col in df.columns for col in ['rd_ratio', 'revenue_yoy', 'eps']):
            complete_mask = (
                (df['rd_ratio'] > 0) & 
                df['revenue_yoy'].notna() & 
                df['eps'].notna()
            )
            report['complete_data'] = int(complete_mask.sum())
        
        return report
