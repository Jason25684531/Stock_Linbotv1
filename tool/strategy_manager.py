"""
StrategyManager - 策略管理器
============================================
負責策略的載入、切換、設定管理

🎯 設計模式: Factory Pattern + Singleton Pattern
- Factory: 根據名稱建立策略物件
- Singleton: 全域唯一的管理器實例
- Lazy Loading: 延遲載入策略物件，避免循環依賴

📁 設定檔位置: strategy_settings.json

🔄 V2 更新 (Phase 2: Multi-Strategy Parallelism):
- 支援同時啟用多個策略 (active_strategies: List[str])
- 向後相容舊格式 (active_strategy: str)
"""

import json
import os
from typing import Optional, Dict, Any, List
from pathlib import Path


class StrategyManager:
    """策略管理器 - 負責策略載入與切換
    
    使用方式：
        manager = StrategyManager()
        
        # 單一策略 (向後相容)
        strategy = manager.get_active_strategy()
        
        # 多策略模式 (V2)
        strategies = manager.get_active_strategies()
        for strategy in strategies:
            candidates = strategy.filter_candidates(df)
    """
    
    # Singleton 實例
    _instance: Optional['StrategyManager'] = None
    
    # 設定檔路徑
    SETTINGS_FILE = 'strategy_settings.json'
    
    # 預設設定 (V2: 使用列表)
    DEFAULT_SETTINGS = {
        'active_strategies': ['v31_hybrid'],  # 🔄 V2: 改為列表
        'version': '2.0',
        'last_updated': None
    }
    
    # 策略註冊表（Lazy Loading，避免循環依賴）
    STRATEGY_REGISTRY = {
        'v31_hybrid': 'tool.strategies.v31_hybrid.V31HybridStrategy',
        'v33_low_vol': 'tool.strategies.v33_low_vol.V33LowVolStrategy',
        'v34_turbo': 'tool.strategies.v34_turbo.V34TurboStrategy',
        'v35_innovation': 'tool.strategies.v35_innovation.V35InnovationStrategy',  # 🧪 V35 研發動能策略
    }
    
    def __new__(cls):
        """Singleton 模式：確保只有一個實例"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """初始化管理器"""
        if self._initialized:
            return
        
        self._initialized = True
        self._strategy_cache: Dict[str, Any] = {}  # 策略物件快取
        self._settings: Optional[Dict] = None
        
        # 確保設定檔存在
        self._ensure_settings_file()
    
    # ============================================
    # 設定檔管理
    # ============================================
    
    def _ensure_settings_file(self):
        """確保設定檔存在，不存在則建立預設設定"""
        if not os.path.exists(self.SETTINGS_FILE):
            print(f"📝 建立策略設定檔: {self.SETTINGS_FILE}")
            self._save_settings(self.DEFAULT_SETTINGS)
    
    def _load_settings(self) -> Dict:
        """載入設定檔 (含向後相容性處理)"""
        try:
            with open(self.SETTINGS_FILE, 'r', encoding='utf-8') as f:
                settings = json.load(f)
                
                # 🔄 V2 向後相容：舊格式 active_strategy (字串) -> active_strategies (列表)
                if 'active_strategy' in settings and 'active_strategies' not in settings:
                    old_strategy = settings.pop('active_strategy')
                    settings['active_strategies'] = [old_strategy] if old_strategy else ['v31_hybrid']
                    settings['version'] = '2.0'
                    print(f"🔄 設定檔已升級至 V2 多策略格式")
                    self._save_settings(settings)
                
                return settings
        except Exception as e:
            print(f"⚠️ 載入設定檔失敗: {e}，使用預設設定")
            return self.DEFAULT_SETTINGS.copy()
    
    def _save_settings(self, settings: Dict):
        """儲存設定檔"""
        try:
            # 更新時間戳
            from datetime import datetime
            settings['last_updated'] = datetime.now().isoformat()
            
            with open(self.SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
            print(f"✅ 策略設定已儲存: {self.SETTINGS_FILE}")
        except Exception as e:
            print(f"❌ 儲存設定檔失敗: {e}")
    
    def get_settings(self) -> Dict:
        """取得當前設定"""
        if self._settings is None:
            self._settings = self._load_settings()
        return self._settings
    
    # ============================================
    # 策略管理
    # ============================================
    
    def get_active_strategy_names(self) -> List[str]:
        """取得所有啟用的策略名稱列表
        
        Returns:
            List[str]: 策略名稱列表，例如 ['v31_hybrid', 'v33_low_vol']
        """
        settings = self.get_settings()
        strategies = settings.get('active_strategies', ['v31_hybrid'])
        
        # 確保返回列表
        if isinstance(strategies, str):
            strategies = [strategies]
        
        return strategies
    
    def get_active_strategy_name(self) -> str:
        """取得第一個啟用的策略名稱 (向後相容)
        
        Returns:
            str: 策略名稱，例如 'v31_hybrid'
        """
        names = self.get_active_strategy_names()
        return names[0] if names else 'v31_hybrid'
    
    def get_active_strategies(self) -> List:
        """取得所有啟用的策略物件列表 (V2 新增)
        
        Returns:
            List[BaseStrategy]: 策略物件實例列表
        """
        strategy_names = self.get_active_strategy_names()
        strategies = []
        
        for name in strategy_names:
            strategy = self._get_or_load_strategy(name)
            if strategy:
                strategies.append(strategy)
        
        # 確保至少有一個策略
        if not strategies:
            fallback = self._get_or_load_strategy('v31_hybrid')
            if fallback:
                strategies.append(fallback)
        
        return strategies
    
    def _get_or_load_strategy(self, strategy_name: str):
        """從快取取得或載入策略
        
        Args:
            strategy_name: 策略名稱
        
        Returns:
            BaseStrategy 實例或 None
        """
        # 檢查快取
        if strategy_name in self._strategy_cache:
            return self._strategy_cache[strategy_name]
        
        # 載入策略
        strategy = self._load_strategy(strategy_name)
        
        if strategy:
            self._strategy_cache[strategy_name] = strategy
        
        return strategy

    def get_active_strategy(self):
        """取得第一個啟用的策略物件 (向後相容)
        
        Returns:
            BaseStrategy: 策略物件實例
        """
        strategies = self.get_active_strategies()
        return strategies[0] if strategies else None
    
    def _load_strategy(self, strategy_name: str):
        """動態載入策略類別
        
        Args:
            strategy_name: 策略名稱
        
        Returns:
            BaseStrategy 實例或 None
        """
        if strategy_name not in self.STRATEGY_REGISTRY:
            print(f"❌ 未知的策略: {strategy_name}")
            return None
        
        try:
            # 動態導入
            module_path = self.STRATEGY_REGISTRY[strategy_name]
            module_name, class_name = module_path.rsplit('.', 1)
            
            # 使用 importlib 動態載入
            import importlib
            module = importlib.import_module(module_name)
            strategy_class = getattr(module, class_name)
            
            # 實例化策略
            strategy = strategy_class()
            
            print(f"✅ 策略已載入: {strategy.display_name} ({strategy_name})")
            return strategy
            
        except Exception as e:
            print(f"❌ 載入策略失敗 ({strategy_name}): {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def set_active_strategies(self, strategy_names: List[str]) -> bool:
        """設定多個啟用的策略 (V2 新增)
        
        Args:
            strategy_names: 要啟用的策略名稱列表
        
        Returns:
            bool: 成功返回 True，失敗返回 False
        """
        if not strategy_names:
            print("❌ 至少需要選擇一個策略")
            return False
        
        # 驗證所有策略
        valid_strategies = []
        for name in strategy_names:
            if name not in self.STRATEGY_REGISTRY:
                print(f"⚠️ 忽略未知策略: {name}")
                continue
            
            strategy = self._get_or_load_strategy(name)
            if strategy:
                valid_strategies.append(name)
        
        if not valid_strategies:
            print("❌ 沒有有效的策略")
            return False
        
        # 更新設定
        settings = self.get_settings()
        settings['active_strategies'] = valid_strategies
        self._save_settings(settings)
        
        # 更新快取
        self._settings = settings
        
        print(f"🔄 已啟用 {len(valid_strategies)} 個策略: {', '.join(valid_strategies)}")
        return True
    
    def set_active_strategy(self, strategy_name: str) -> bool:
        """切換為單一策略 (向後相容)
        
        Args:
            strategy_name: 要切換到的策略名稱
        
        Returns:
            bool: 成功返回 True，失敗返回 False
        """
        return self.set_active_strategies([strategy_name])
    
    def list_available_strategies(self) -> Dict[str, str]:
        """列出所有可用策略
        
        Returns:
            Dict: {策略名稱: 策略描述}
        """
        result = {}
        for name in self.STRATEGY_REGISTRY.keys():
            strategy = self._load_strategy(name)
            if strategy:
                result[name] = strategy.display_name
        return result
    
    # ============================================
    # 便捷方法
    # ============================================
    
    def get_strategy_config(self) -> Dict[str, Any]:
        """取得當前策略的完整設定
        
        Returns:
            Dict: 策略設定字典
        """
        strategy = self.get_active_strategy()
        return strategy.get_config() if strategy else {}
    
    def reset_to_default(self):
        """重置為預設策略"""
        self.set_active_strategies(['v31_hybrid'])
    
    def list_strategies(self) -> List[str]:
        """列出所有可用策略名稱 (供前端使用)
        
        Returns:
            List[str]: 策略名稱列表
        """
        return list(self.STRATEGY_REGISTRY.keys())
    
    def __repr__(self) -> str:
        strategy_names = self.get_active_strategy_names()
        return f"<StrategyManager: active={strategy_names}>"


# ============================================
# 全域便捷函數
# ============================================

def get_active_strategy():
    """全域便捷函數：取得當前第一個策略 (向後相容)
    
    Returns:
        BaseStrategy: 當前啟用的策略物件
    """
    manager = StrategyManager()
    return manager.get_active_strategy()


def get_active_strategies():
    """全域便捷函數：取得所有啟用的策略 (V2 新增)
    
    Returns:
        List[BaseStrategy]: 所有啟用的策略物件列表
    """
    manager = StrategyManager()
    return manager.get_active_strategies()


def get_strategy_manager() -> StrategyManager:
    """全域便捷函數：取得管理器實例
    
    Returns:
        StrategyManager: 管理器實例
    """
    return StrategyManager()
