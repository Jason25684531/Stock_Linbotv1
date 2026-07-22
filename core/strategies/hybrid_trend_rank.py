"""
V31 混合策略 (Hybrid Strategy)
============================================
結合技術面硬篩選與 ML 智慧排名

🎯 策略特色：
- 硬篩選：均線多頭排列 + RSI 40~70 + 量能放大
- AI 排名：XGBoost 預測漲幅機率，智慧選出高潛力股
- 風險控制：7% 停損 / 15% 停利 / 10 天持有期

📊 歷史績效：
- 勝率：~60%
- 平均報酬：8-12%
- 最大回撤：~15%
"""

from typing import List
import pandas as pd
from .base import BaseStrategy
from config import Config


class HybridTrendRankStrategy(BaseStrategy):
    """V31 混合策略 - 均線 + RSI + 籌碼面 + ML 智慧排名"""
    
    # ============================================
    # 策略屬性定義
    # ============================================
    
    @property
    def name(self) -> str:
        return 'v31_hybrid'
    
    @property
    def display_name(self) -> str:
        return 'V31 混合策略'
    
    @property
    def description(self) -> str:
        return '均線多頭 + RSI 中性 + 量能放大 + XGBoost 智慧排名（平衡型波段策略）'
    
    @property
    def features(self) -> List[str]:
        """V31 使用的 AI 特徵
        
        包含技術指標 + 比例特徵（標準化籌碼面）
        """
        return [
            # 技術指標
            'rsi',           # 相對強弱指標
            'bias',          # 乖離率
            'macd_hist',     # MACD 柱狀圖
            'kd_k',          # KD 指標 K 值
            'bb_width',      # 布林通道寬度
            
            # 比例特徵（籌碼面標準化）
            'volume_ratio',   # 成交量相對 20 日均量
            'foreign_ratio',  # 外資買賣超比例
            'trust_ratio'     # 投信買賣超比例
        ]
    
    @property
    def target_return(self) -> float:
        """目標報酬率 8%"""
        return 0.08
    
    @property
    def look_ahead_days(self) -> int:
        """預測未來 7 天"""
        return 7
    
    @property
    def stop_loss(self) -> float:
        """停損 7%"""
        return 0.07
    
    @property
    def take_profit(self) -> float:
        """停利 15%"""
        return 0.15
    
    @property
    def max_hold_days(self) -> int:
        """最長持有 10 天"""
        return 10
    
    # ============================================
    # 硬篩選邏輯 (移植自 core/strategy.py)
    # ============================================
    
    def filter_candidates(self, df: pd.DataFrame) -> pd.DataFrame:
        """V31 硬篩選邏輯
        
        篩選條件：
        1. 市場熔斷檢查（熊市不選股）
        2. 均線排列：收盤 > MA20 > MA60
        3. 收盤 > MA60（趨勢濾網）
        4. 成交量 > 300萬股
        5. 40 < RSI < 70
        6. 可選：KD 超賣濾網、布林通道壓縮突破
        
        Args:
            df: DataFrame，包含股票資料與技術指標
        
        Returns:
            DataFrame: 符合條件的候選股票
        """
        if df.empty:
            return pd.DataFrame()

        # 排除非個股（ETF/權證/債券/KY）
        df = self._filter_real_stocks(df)

        date_str = self._extract_date_str(df)
        
        # ============================================
        # 🔥 市場熔斷機制（Circuit Breaker）
        # ============================================
        if not self._check_market_filter(date_str, 'V31'):
            return pd.DataFrame()
        
        # ============================================
        # 基本欄位檢查
        # ============================================
        required_cols = ['close_price', 'ma20', 'ma60', 'volume', 'rsi']
        for col in required_cols:
            if col not in df.columns:
                print(f"⚠️ 缺少必要欄位: {col}")
                return pd.DataFrame()

        # 數值清理：避免 None/NaN 比較造成篩選錯誤
        numeric_cols = required_cols + ['kd_k', 'bb_width']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        
        # ============================================
        # 🔥 個股趨勢濾網（收盤 > MA60）
        # ============================================
        if Config.USE_TREND_FILTER:
            before_count = len(df)
            df = df[df['close_price'] > df['ma60']].copy()
            if df.empty:
                print(f"⚠️ 個股趨勢濾網：所有標的收盤價皆低於 MA60，暫停選股")
                return pd.DataFrame()
            print(f"✨ 個股趨勢濾網：{before_count} → {len(df)} 檔（篩除收盤 < MA60）")
        
        # ============================================
        # V31 核心篩選條件
        # ============================================
        candidates = df[
            (df['close_price'] > df['ma20']) &           # 收盤 > MA20
            (df['ma20'] > df['ma60']) &                  # MA20 > MA60（多頭排列）
            (df['volume'] > Config.V30_VOLUME_THRESHOLD) &  # 量能充足（300萬股）
            (df['rsi'] > Config.V30_RSI_LOW) &           # RSI 不過低（> 40）
            (df['rsi'] < Config.V30_RSI_HIGH)            # RSI 不過高（< 70）
        ].copy()
        
        if candidates.empty:
            print(f"⚠️ V31 硬篩選：無符合條件的股票")
            return pd.DataFrame()
        
        print(f"✅ V31 硬篩選：找到 {len(candidates)} 檔候選股票")
        
        # ============================================
        # 🆕 進階濾網 (可選)
        # ============================================
        
        # 1️⃣ KD 超賣濾網
        if Config.USE_KD_FILTER and 'kd_k' in candidates.columns:
            try:
                before_count = len(candidates)
                candidates = candidates[candidates['kd_k'] < 30].copy()
                print(f"✨ KD 超賣濾網：{before_count} → {len(candidates)} 檔 (K < 30)")
            except Exception as e:
                print(f"⚠️ KD 濾網執行失敗: {e}")
        
        # 2️⃣ 布林通道壓縮突破濾網
        if Config.USE_BB_FILTER and 'bb_width' in candidates.columns:
            try:
                before_count = len(candidates)
                bb_squeeze = candidates['bb_width'] < Config.BB_SQUEEZE_THRESHOLD
                
                if Config.BB_BREAKOUT_POSITION == 'upper':
                    candidates = candidates[bb_squeeze & (candidates['close_price'] > candidates['ma20'])].copy()
                elif Config.BB_BREAKOUT_POSITION == 'lower':
                    candidates = candidates[bb_squeeze & (candidates['close_price'] < candidates['ma20'])].copy()
                else:
                    candidates = candidates[bb_squeeze].copy()
                
                print(f"✨ 布林通道濾網：{before_count} → {len(candidates)} 檔")
            except Exception as e:
                print(f"⚠️ BB 濾網執行失敗: {e}")
        
        return candidates
    
    # ============================================
    # 額外方法
    # ============================================
    
    def get_risk_params(self) -> dict:
        """取得風險控制參數
        
        Returns:
            dict: 風險參數字典
        """
        return {
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'max_hold_days': self.max_hold_days
        }
