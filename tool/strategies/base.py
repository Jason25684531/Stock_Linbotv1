"""
BaseStrategy - 策略抽象基類
============================================
定義所有策略必須實現的介面

🎯 設計模式: Strategy Pattern (策略模式)
- 抽象類別定義共同介面
- 各策略類別實現具體邏輯
- Manager 負責動態切換

🔄 V35 架構清理:
- 新增共用方法 _extract_date_str() / _check_market_filter()
- 消除各策略中重複的日期提取與市場熔斷檢查邏輯
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Tuple
import pandas as pd


class BaseStrategy(ABC):
    """策略抽象基類
    
    所有策略必須繼承此類並實現 filter_candidates 方法
    """
    
    def __init__(self):
        """初始化策略"""
        self._validate_attributes()
    
    # ============================================
    # 抽象屬性 (子類必須定義)
    # ============================================
    
    @property
    @abstractmethod
    def name(self) -> str:
        """策略名稱 (例如: 'v31_hybrid')"""
        pass
    
    @property
    @abstractmethod
    def display_name(self) -> str:
        """策略顯示名稱 (例如: 'V31 混合策略')"""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """策略描述"""
        pass
    
    @property
    @abstractmethod
    def features(self) -> List[str]:
        """AI 模型使用的特徵列表
        
        Returns:
            List[str]: 特徵名稱列表，例如:
                ['rsi', 'bias', 'macd_hist', 'volume_ratio', ...]
        """
        pass
    
    @property
    @abstractmethod
    def target_return(self) -> float:
        """目標報酬率 (用於訓練模型的標籤定義)
        
        Returns:
            float: 目標報酬率，例如 0.08 代表 8%
        """
        pass
    
    @property
    @abstractmethod
    def look_ahead_days(self) -> int:
        """預測天數 (用於計算未來收益)
        
        Returns:
            int: 向前看的天數，例如 7 代表預測未來 7 天
        """
        pass
    
    # ============================================
    # 可選屬性 (子類可覆寫)
    # ============================================
    
    @property
    def stop_loss(self) -> float:
        """停損比例 (預設 7%)"""
        return 0.07
    
    @property
    def take_profit(self) -> float:
        """停利比例 (預設 15%)"""
        return 0.15
    
    @property
    def max_hold_days(self) -> int:
        """最長持有天數 (預設 10 天)"""
        return 10
    
    # ============================================
    # 抽象方法 (子類必須實現)
    # ============================================
    
    @abstractmethod
    def filter_candidates(self, df: pd.DataFrame) -> pd.DataFrame:
        """硬篩選邏輯 (Strategy-specific Filtering)
        
        每個策略實現自己的篩選條件，例如：
        - V31: 均線多頭排列 + RSI 40~70 + 量能放大
        - V33: NATR < 4% + 收盤 > MA60
        - V34: revenue_yoy > 30% + 60日新高
        
        Args:
            df: DataFrame，包含股票資料與技術指標
        
        Returns:
            DataFrame: 篩選後的候選股票
        """
        pass
    
    # ============================================
    # 出場邏輯 (Exit Signal - 階梯式移動停損)
    # ============================================
    
    def check_exit_signal(
        self,
        stock_id: str,
        current_price: float,
        current_date: str,
        position_info: Dict[str, Any],
        market_trend: str = 'BULL'
    ) -> Tuple[str, str, float]:
        """判斷是否賣出（階梯式移動停損 + 持有天數檢查）
        
        預設實作包含：
        1. 階梯式移動停損 (Level 1: +10%, Level 2: +20%, Level 3: +30%)
        2. 固定停損 / 停利
        3. 最長持有天數
        4. 趨勢轉空且虧損時強制出場
        
        子類別可覆寫此方法以自訂出場規則。
        
        Args:
            stock_id: 股票代碼
            current_price: 當前價格
            current_date: 當前日期字串
            position_info: 持倉資訊 dict, 需包含:
                - cost: 買入成本
                - stop_loss: 當前停損價
                - days: 已持有天數
                - highest: 歷史最高價
            market_trend: 市場趨勢 ('BULL' / 'BEAR' / 'NEUTRAL')
        
        Returns:
            Tuple[action, reason, updated_stop_loss]:
                action: 'SELL' 或 'HOLD'
                reason: 賣出原因 (如 '停損', '停利', '時間到', '趨勢轉空')
                updated_stop_loss: 更新後的停損價
        """
        cost = position_info['cost']
        old_stop = position_info['stop_loss']
        days = position_info['days']
        change = (current_price - cost) / cost
        
        new_stop = old_stop

        # ============================================
        # 階梯式移動停損 (Stepped Trailing Stop)
        # ============================================
        if change >= 0.30:
            # Level 3: 獲利 >= 30%，鎖定 25% 利潤
            candidate = cost * 1.25
            if candidate > new_stop:
                new_stop = candidate
                print(f"  🔒 {stock_id} 進入 Level 3，停損上移至 {new_stop:.2f} (鎖定+25%)")
        elif change >= 0.20:
            # Level 2: 獲利 >= 20%，鎖定 15% 利潤
            candidate = cost * 1.15
            if candidate > new_stop:
                new_stop = candidate
                print(f"  🔒 {stock_id} 進入 Level 2，停損上移至 {new_stop:.2f} (鎖定+15%)")
        elif change >= 0.10:
            # Level 1: 獲利 >= 10%，保本 + 手續費
            candidate = cost * 1.01
            if candidate > new_stop:
                new_stop = candidate
                print(f"  🔒 {stock_id} 進入 Level 1，停損上移至 {new_stop:.2f} (保本+1%)")
        
        # ============================================
        # 賣出判斷
        # ============================================
        
        # 1. 觸及停損
        if current_price <= new_stop:
            return ('SELL', '停損', new_stop)
        
        # 2. 達到停利目標
        if self.take_profit > 0 and change >= self.take_profit:
            return ('SELL', '停利', new_stop)
        
        # 3. 超過最長持有天數
        if days >= self.max_hold_days:
            return ('SELL', '時間到', new_stop)
        
        # 4. 市場趨勢轉空且虧損
        if market_trend == 'BEAR' and change < 0:
            return ('SELL', '趨勢轉空', new_stop)
        
        return ('HOLD', '', new_stop)

    # ============================================
    # 共用方法 (所有策略可用)
    # ============================================
    
    def _extract_date_str(self, df: pd.DataFrame) -> str:
        """從 DataFrame 提取日期字串（共用邏輯，避免各策略重複實作）
        
        Args:
            df: 含有 trade_date 欄位的 DataFrame
        
        Returns:
            str: 日期字串 (YYYY-MM-DD 格式)
        """
        date_val = df['trade_date'].max()
        if hasattr(date_val, 'strftime'):
            return date_val.strftime('%Y-%m-%d')
        return str(date_val)
    
    def _check_market_filter(self, date_str: str, strategy_label: str = '') -> bool:
        """共用市場熔斷檢查（大盤 < MA60 時禁止買入）
        
        Args:
            date_str: 日期字串
            strategy_label: 策略標籤（用於日誌）
        
        Returns:
            bool: True 表示市場允許買入，False 表示觸發熔斷
        """
        # ============================================
        # [TEST MODE] 測試模式強制覆蓋 - 僅供開發測試使用
        # ============================================
        import os
        if os.getenv('FORCE_BULL_MARKET', 'false').lower() == 'true':
            print(f"⚡ [測試模式] 強制設定市場為多頭 (BULL) - 忽略實際市場狀態")
            return True
        
        from config import Config
        if not Config.USE_MARKET_FILTER:
            return True
        
        try:
            from tool.db_helper import get_market_trend
            market_trend = get_market_trend(date_str)
            
            label = strategy_label or self.name
            if market_trend != 'BULL':
                print(f"⛔ 市場熔斷觸發（{date_str}）：大盤未處於多頭，{label} 暫停選股")
                return False
            else:
                print(f"✅ 市場狀態良好（{date_str}）：大盤處於多頭，允許選股")
                return True
        except Exception as e:
            print(f"⚠️ 市場趨勢檢查失敗: {e}")
            return True  # 失敗時不阻擋

    def _validate_attributes(self):
        """驗證策略屬性是否完整"""
        required_attrs = [
            'name', 'display_name', 'description', 
            'features', 'target_return', 'look_ahead_days'
        ]
        for attr in required_attrs:
            if not hasattr(self, attr):
                raise NotImplementedError(
                    f"策略必須定義 '{attr}' 屬性"
                )
    
    def get_config(self) -> Dict[str, Any]:
        """取得策略完整設定 (用於序列化)
        
        Returns:
            Dict: 策略設定字典
        """
        return {
            'name': self.name,
            'display_name': self.display_name,
            'description': self.description,
            'features': self.features,
            'target_return': self.target_return,
            'look_ahead_days': self.look_ahead_days,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'max_hold_days': self.max_hold_days
        }
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}: {self.display_name}>"
