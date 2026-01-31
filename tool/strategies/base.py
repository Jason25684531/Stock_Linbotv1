"""
BaseStrategy - 策略抽象基類
============================================
定義所有策略必須實現的介面

🎯 設計模式: Strategy Pattern (策略模式)
- 抽象類別定義共同介面
- 各策略類別實現具體邏輯
- Manager 負責動態切換
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any
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
    # 共用方法 (所有策略可用)
    # ============================================
    
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
