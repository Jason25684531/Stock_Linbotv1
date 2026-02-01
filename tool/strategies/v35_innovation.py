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
        V35 篩選邏輯 - 基本面 + 技術面雙重驗證
        
        Args:
            df: 包含所有指標的 DataFrame
                必要欄位：
                - rd_ratio: 研發費用佔比（來自 financial_statements）
                - revenue_yoy: 營收年增率
                - eps: 每股盈餘
                - close: 收盤價
                - ma60: 60日均線
                - volume_ratio: 成交量比例
        
        Returns:
            篩選後的 DataFrame
        """
        if df.empty:
            return df
        
        print(f"\n🔍 [V35] 原始候選股票數：{len(df)}")
        
        # ============================================
        # 階段 1：基本面篩選（嚴格）
        # ============================================
        
        # 檢查必要欄位
        required_cols = ['rd_ratio', 'revenue_yoy', 'eps', 'close', 'ma60', 'volume_ratio']
        missing_cols = [col for col in required_cols if col not in df.columns]
        
        if missing_cols:
            print(f"⚠️ [V35] 缺少必要欄位：{missing_cols}")
            # 嘗試填補預設值
            if 'rd_ratio' in missing_cols:
                df['rd_ratio'] = 0.0  # 假設無研發
            if 'eps' in missing_cols:
                df['eps'] = 0.0
        
        # 過濾條件 1：研發投入比例 > 3%
        mask_rd = df['rd_ratio'] > 0.03
        df_rd = df[mask_rd].copy()
        print(f"  ✓ 研發投入 > 3%：{len(df_rd)} 檔 (剩餘 {len(df_rd)/len(df)*100:.1f}%)")
        
        if df_rd.empty:
            print("  ⚠️ 無股票符合研發條件")
            return pd.DataFrame()
        
        # 過濾條件 2：營收正成長（YoY > 0）
        mask_revenue = df_rd['revenue_yoy'] > 0
        df_revenue = df_rd[mask_revenue].copy()
        print(f"  ✓ 營收正成長：{len(df_revenue)} 檔 (剩餘 {len(df_revenue)/len(df)*100:.1f}%)")
        
        if df_revenue.empty:
            print("  ⚠️ 無股票符合營收成長條件")
            return pd.DataFrame()
        
        # 過濾條件 3：獲利能力（EPS > 0）
        mask_eps = df_revenue['eps'] > 0
        df_eps = df_revenue[mask_eps].copy()
        print(f"  ✓ 有獲利 (EPS>0)：{len(df_eps)} 檔 (剩餘 {len(df_eps)/len(df)*100:.1f}%)")
        
        if df_eps.empty:
            print("  ⚠️ 無獲利股票")
            return pd.DataFrame()
        
        # ============================================
        # 階段 2：技術面篩選（趨勢確認）
        # ============================================
        
        # 過濾條件 4：多頭排列（收盤 > 60 日均線）
        mask_ma = df_eps['close'] > df_eps['ma60']
        df_ma = df_eps[mask_ma].copy()
        print(f"  ✓ 多頭排列 (>MA60)：{len(df_ma)} 檔 (剩餘 {len(df_ma)/len(df)*100:.1f}%)")
        
        if df_ma.empty:
            print("  ⚠️ 無股票在多頭趨勢")
            return pd.DataFrame()
        
        # 過濾條件 5：成交量足夠（流動性）
        mask_vol = df_ma['volume_ratio'] > 0.8
        df_final = df_ma[mask_vol].copy()
        print(f"  ✓ 成交量充足：{len(df_final)} 檔 (剩餘 {len(df_final)/len(df)*100:.1f}%)")
        
        # ============================================
        # 階段 3：計算品質評分（排序用）
        # ============================================
        
        if not df_final.empty:
            # 品質評分 = 研發佔比*40% + 營收成長*30% + EPS*30%
            df_final = df_final.copy()
            
            # 正規化各指標 (0-1)
            rd_norm = (df_final['rd_ratio'] - df_final['rd_ratio'].min()) / \
                      (df_final['rd_ratio'].max() - df_final['rd_ratio'].min() + 1e-9)
            
            revenue_norm = (df_final['revenue_yoy'] - df_final['revenue_yoy'].min()) / \
                           (df_final['revenue_yoy'].max() - df_final['revenue_yoy'].min() + 1e-9)
            
            eps_norm = (df_final['eps'] - df_final['eps'].min()) / \
                       (df_final['eps'].max() - df_final['eps'].min() + 1e-9)
            
            # 計算綜合評分
            df_final['v35_quality_score'] = (
                rd_norm * 0.4 +         # 研發投入權重 40%
                revenue_norm * 0.3 +    # 營收成長權重 30%
                eps_norm * 0.3          # 獲利能力權重 30%
            )
            
            # 依品質評分排序
            df_final = df_final.sort_values('v35_quality_score', ascending=False)
            
            print(f"\n✅ [V35] 最終篩選：{len(df_final)} 檔")
            print(f"  • 平均研發佔比：{df_final['rd_ratio'].mean()*100:.2f}%")
            print(f"  • 平均營收成長：{df_final['revenue_yoy'].mean():.1f}%")
            print(f"  • 平均 EPS：{df_final['eps'].mean():.2f} 元")
        else:
            print(f"\n⚠️ [V35] 無股票通過所有條件")
        
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
        
        msg = f"""
🧪 研發動能股 ({stock_id})
━━━━━━━━━━━━━━━
📊 基本面：
  • 研發佔比：{rd_ratio:.2f}%
  • 營收成長：{revenue_yoy:+.1f}%
  • 每股盈餘：{eps:.2f} 元

⭐ 品質評分：{quality_score:.1f}/100

💡 策略：V35 中長線（持有 30-60 天）
  停損 10% | 目標 20%
"""
        return msg.strip()
    
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
            report['has_rd_data'] = (df['rd_ratio'] > 0).sum()
        
        if 'revenue_yoy' in df.columns:
            report['has_revenue_data'] = df['revenue_yoy'].notna().sum()
        
        if 'eps' in df.columns:
            report['has_eps_data'] = df['eps'].notna().sum()
        
        # 完整資料（三者皆有）
        if all(col in df.columns for col in ['rd_ratio', 'revenue_yoy', 'eps']):
            complete_mask = (
                (df['rd_ratio'] > 0) & 
                df['revenue_yoy'].notna() & 
                df['eps'].notna()
            )
            report['complete_data'] = complete_mask.sum()
        
        return report


# ===========================================
# 策略工廠註冊（自動註冊）
# ===========================================
def register_strategy():
    """註冊策略到策略管理器"""
    from tool.strategy_manager import register_strategy as register
    register(V35InnovationStrategy())


# 模組載入時自動註冊
try:
    register_strategy()
except ImportError:
    pass  # 避免循環依賴
