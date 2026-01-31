"""
V33 低波動穩健策略 (Low Volatility Strategy)
============================================
專注於低波動、穩健成長的股票

🎯 策略特色：
- 核心邏輯：波動度低 (NATR < 4%) + 多頭趨勢 (收盤 > MA60)
- 風險控制：嚴格停損 5%，追求穩定月報酬 3-5%
- 適用場景：保守型投資人、熊市避險

📊 預期績效：
- 勝率：~70%（高勝率，低報酬）
- 平均報酬：3-5%
- 最大回撤：~8%（低回撤）
- 持有期：較長（10-15 天）

🔍 篩選邏輯：
1. NATR < 4%（波動度低於 4%，價格穩定）
2. 收盤價 > MA60（處於多頭趨勢）
3. 成交量 > 500 萬股（排除冷門股）
4. STD_20 排序（選波動最低的）
"""

from typing import List
import pandas as pd
from .base import BaseStrategy
from config import Config


class V33LowVolStrategy(BaseStrategy):
    """V33 低波動穩健策略 - 追求低回撤、穩定報酬"""
    
    # ============================================
    # 策略屬性定義
    # ============================================
    
    @property
    def name(self) -> str:
        return 'v33_low_vol'
    
    @property
    def display_name(self) -> str:
        return 'V33 低波動穩健策略'
    
    @property
    def description(self) -> str:
        return '低波動 (NATR<4%) + 多頭趨勢 (收盤>MA60)，追求穩定報酬、低回撤'
    
    @property
    def features(self) -> List[str]:
        """V33 使用的 AI 特徵
        
        強調波動度與穩定性指標
        """
        return [
            # 核心：波動度指標
            'natr',          # 標準化波動度（核心）
            'std_20',        # 20日標準差（輔助）
            
            # 技術指標
            'rsi',           # 相對強弱指標
            'bias',          # 乖離率
            'macd_hist',     # MACD 柱狀圖
            'kd_k',          # KD 指標
            'bb_width',      # 布林通道寬度
            
            # 籌碼面
            'volume_ratio',  # 成交量比例
        ]
    
    @property
    def target_return(self) -> float:
        """目標報酬率 5%（保守）"""
        return 0.05
    
    @property
    def look_ahead_days(self) -> int:
        """預測未來 10 天（較長持有期）"""
        return 10
    
    @property
    def stop_loss(self) -> float:
        """嚴格停損 5%"""
        return 0.05
    
    @property
    def take_profit(self) -> float:
        """停利 10%"""
        return 0.10
    
    @property
    def max_hold_days(self) -> int:
        """最長持有 15 天"""
        return 15
    
    # ============================================
    # V33 核心篩選邏輯
    # ============================================
    
    def filter_candidates(self, df: pd.DataFrame) -> pd.DataFrame:
        """V33 低波動篩選邏輯
        
        篩選條件：
        1. NATR < 4%（低波動核心）
        2. 收盤價 > MA60（多頭趨勢）
        3. 成交量 > 500 萬股（排除冷門股）
        4. RSI 40~70（避免超買超賣）
        
        Args:
            df: DataFrame，包含股票資料與技術指標
        
        Returns:
            DataFrame: 符合條件的候選股票（依 STD_20 升序排列）
        """
        if df.empty:
            return pd.DataFrame()
        
        # ============================================
        # 欄位檢查與優雅降級
        # ============================================
        required_cols = ['close_price', 'ma60', 'volume']
        optional_cols = ['natr', 'std_20', 'rsi']
        
        # 檢查必要欄位
        for col in required_cols:
            if col not in df.columns:
                print(f"⚠️ V33: 缺少必要欄位 '{col}'，無法篩選")
                return pd.DataFrame()
        
        # 檢查可選欄位
        missing_optional = [col for col in optional_cols if col not in df.columns]
        if missing_optional:
            print(f"⚠️ V33: 缺少指標欄位 {missing_optional}，將使用基本篩選")
        
        # ============================================
        # 取得日期
        # ============================================
        date_str = df['trade_date'].max()
        if hasattr(date_str, 'strftime'):
            date_str = date_str.strftime('%Y-%m-%d')
        else:
            date_str = str(date_str)
        
        print(f"\n🔍 V33 低波動策略篩選 ({date_str})")
        print(f"   目標：低波動 + 穩定成長")
        
        # ============================================
        # 市場熔斷檢查（可選）
        # ============================================
        if Config.USE_MARKET_FILTER:
            try:
                from tool.db_helper import get_market_trend
                market_trend = get_market_trend(date_str)
                
                if market_trend == 'BEAR':
                    print(f"⛔ 市場熊市：V33 暫停選股（大盤 < MA60）")
                    return pd.DataFrame()
                else:
                    print(f"✅ 市場狀態：{market_trend}")
            except Exception as e:
                print(f"⚠️ 市場趨勢檢查失敗: {e}")
        
        # ============================================
        # V33 核心篩選
        # ============================================
        
        # 1. 基本篩選：多頭趨勢 + 量能
        candidates = df[
            (df['close_price'] > df['ma60']) &      # 收盤 > MA60（多頭）
            (df['volume'] > 500_0000)                # 量能 > 500萬（排除冷門股）
        ].copy()
        
        if candidates.empty:
            print(f"   ❌ 基本篩選：無符合條件的股票（需收盤>MA60 且量能足夠）")
            return pd.DataFrame()
        
        print(f"   ✅ 基本篩選：{len(candidates)} 檔（收盤>MA60 + 量能>500萬）")
        
        # 2. NATR 篩選（核心）
        if 'natr' in candidates.columns:
            before_count = len(candidates)
            candidates = candidates[candidates['natr'] < 4.0].copy()
            print(f"   ✅ NATR 篩選：{before_count} → {len(candidates)} 檔（NATR < 4%）")
            
            if candidates.empty:
                print(f"   ❌ 無低波動股票（所有 NATR ≥ 4%）")
                return pd.DataFrame()
        else:
            print(f"   ⚠️ 跳過 NATR 篩選（欄位不存在）")
        
        # 3. RSI 篩選（避免超買超賣）
        if 'rsi' in candidates.columns:
            before_count = len(candidates)
            candidates = candidates[
                (candidates['rsi'] > 40) & 
                (candidates['rsi'] < 70)
            ].copy()
            print(f"   ✅ RSI 篩選：{before_count} → {len(candidates)} 檔（40 < RSI < 70）")
        
        if candidates.empty:
            print(f"   ❌ RSI 篩選後無剩餘股票")
            return pd.DataFrame()
        
        # ============================================
        # 排序：依波動度由低到高
        # ============================================
        if 'std_20' in candidates.columns:
            candidates = candidates.sort_values('std_20', ascending=True)
            print(f"   📊 依 STD_20 升序排列（優先選波動最低的）")
        elif 'natr' in candidates.columns:
            candidates = candidates.sort_values('natr', ascending=True)
            print(f"   📊 依 NATR 升序排列")
        
        print(f"\n✅ V33 篩選完成：{len(candidates)} 檔低波動候選股票")
        
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
            'type': '穩健型',
            'risk_level': '低',
            'target_return': f'{self.target_return*100}%',
            'max_drawdown_target': '8%',
            'win_rate_target': '70%',
            'holding_period': f'{self.max_hold_days} 天',
            'core_logic': 'NATR < 4% + 收盤 > MA60',
            'suitable_for': '保守型投資人、熊市避險'
        }
