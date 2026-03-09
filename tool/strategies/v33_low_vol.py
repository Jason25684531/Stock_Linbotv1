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
1. NATR < 3.5%（波動度更低，降低雜訊）
2. 收盤價 > MA20 > MA60（確認趨勢強度）
3. 成交量 > 500 萬股 + 量比 > 1.0（排除冷門與弱量）
4. RSI 45~65 + STD_20 排序（選波動最低的穩健股）
"""

from typing import List
import pandas as pd
from config import Config
from .base import BaseStrategy


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
        return '低波動 (NATR<3.5%) + 趨勢強化 (收盤>MA20>MA60)，追求穩定報酬、低回撤'
    
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
        """停損 6%（第二階段微調：降低震盪洗出）"""
        return 0.06
    
    @property
    def take_profit(self) -> float:
        """停利 12%（第二階段微調：放大趨勢段收益）"""
        return 0.12
    
    @property
    def max_hold_days(self) -> int:
        """最長持有 12 天（第二階段微調：加速資金輪動）"""
        return 12
    
    # ============================================
    # V33 核心篩選邏輯
    # ============================================
    
    def filter_candidates(self, df: pd.DataFrame) -> pd.DataFrame:
        """V33 低波動篩選邏輯
        
        篩選條件：
        1. NATR < 3.5%（低波動核心）
        2. 收盤價 > MA20 > MA60（多頭趨勢強化）
        3. 成交量 > 500 萬股 + 量比 > 1.0（排除冷門弱勢股）
        4. RSI 45~65（避免極端位置）
        
        Args:
            df: DataFrame，包含股票資料與技術指標
        
        Returns:
            DataFrame: 符合條件的候選股票（依 STD_20 升序排列）
        """
        if df.empty:
            return pd.DataFrame()

        # 排除非個股（ETF/權證/債券/KY）
        df = self._filter_real_stocks(df)

        # ============================================
        # 欄位檢查與優雅降級
        # ============================================
        required_cols = ['close_price', 'ma60', 'volume']
        optional_cols = ['natr', 'std_20', 'rsi', 'ma20', 'volume_ratio', 'macd_hist', 'bias']
        
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
        date_str = self._extract_date_str(df)
        
        print(f"\n🔍 V33 低波動策略篩選 ({date_str})")
        print(f"   目標：低波動 + 穩定成長")
        
        # ============================================
        # 市場熔斷檢查（可選）
        # ============================================
        if not self._check_market_filter(date_str, 'V33'):
            return pd.DataFrame()
        
        # ============================================
        # 資料清理：處理 None/NaN 值
        # ============================================
        numeric_cols = ['close_price', 'ma20', 'ma60', 'volume', 'volume_ratio', 'natr', 'rsi', 'std_20', 'macd_hist', 'bias']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        
        # ============================================
        # V33 核心篩選
        # ============================================
        
        # 1. 基本篩選：多頭趨勢 + 量能
        trend_mask = (df['close_price'] > df['ma60'])
        if 'ma20' in df.columns:
            trend_mask = trend_mask & (df['close_price'] > df['ma20']) & (df['ma20'] > df['ma60'])

        liquidity_mask = (df['volume'] > Config.V33_VOLUME_THRESHOLD)
        if 'volume_ratio' in df.columns:
            liquidity_mask = liquidity_mask & (df['volume_ratio'] > Config.V33_VOLUME_RATIO_MIN)

        candidates = df[trend_mask & liquidity_mask].copy()
        
        if candidates.empty:
            print(f"   ❌ 基本篩選：無符合條件的股票（需收盤>MA60 且量能足夠）")
            return pd.DataFrame()
        
        trend_desc = "收盤>MA20>MA60" if 'ma20' in df.columns else "收盤>MA60"
        vol_desc = f"量能>{Config.V33_VOLUME_THRESHOLD//10000}萬 + 量比>{Config.V33_VOLUME_RATIO_MIN}" if 'volume_ratio' in df.columns else f"量能>{Config.V33_VOLUME_THRESHOLD//10000}萬"
        print(f"   ✅ 基本篩選：{len(candidates)} 檔（{trend_desc} + {vol_desc}）")
        
        # 2. NATR 篩選（核心）
        if 'natr' in candidates.columns:
            before_count = len(candidates)
            candidates = candidates[candidates['natr'] < Config.V33_NATR_MAX].copy()
            print(f"   ✅ NATR 篩選：{before_count} → {len(candidates)} 檔（NATR < {Config.V33_NATR_MAX}%）")
            
            if candidates.empty:
                print(f"   ❌ 無低波動股票（所有 NATR ≥ 3.5%）")
                return pd.DataFrame()
        else:
            print(f"   ⚠️ 跳過 NATR 篩選（欄位不存在）")
        
        # 3. RSI 篩選（避免超買超賣）
        if 'rsi' in candidates.columns:
            before_count = len(candidates)
            candidates = candidates[
                (candidates['rsi'] > Config.V33_RSI_LOW) & 
                (candidates['rsi'] < Config.V33_RSI_HIGH)
            ].copy()
            print(f"   ✅ RSI 篩選：{before_count} → {len(candidates)} 檔（{Config.V33_RSI_LOW} < RSI < {Config.V33_RSI_HIGH}）")
        
        if candidates.empty:
            print(f"   ❌ RSI 篩選後無剩餘股票")
            return pd.DataFrame()

        # 4.5 動能/乖離微調（第二階段）：避免逆勢下跌股
        if 'macd_hist' in candidates.columns:
            before_count = len(candidates)
            candidates = candidates[candidates['macd_hist'] > Config.V33_MACD_HIST_MIN].copy()
            print(f"   ✅ MACD 篩選：{before_count} → {len(candidates)} 檔（MACD_hist > {Config.V33_MACD_HIST_MIN}）")

        if 'bias' in candidates.columns:
            before_count = len(candidates)
            candidates = candidates[(candidates['bias'] > Config.V33_BIAS_LOW) & (candidates['bias'] < Config.V33_BIAS_HIGH)].copy()
            print(f"   ✅ BIAS 篩選：{before_count} → {len(candidates)} 檔（{Config.V33_BIAS_LOW} < BIAS < {Config.V33_BIAS_HIGH}）")

        if candidates.empty:
            print(f"   ❌ 動能微調後無剩餘股票")
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
            'core_logic': 'NATR < 3.5% + 收盤 > MA20 > MA60 + 量比 > 1.0',
            'suitable_for': '保守型投資人、熊市避險'
        }
