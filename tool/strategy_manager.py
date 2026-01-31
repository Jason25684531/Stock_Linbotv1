"""
StrategyManager - 策略管理器
============================================
負責策略的載入、切換、設定管理

🎯 設計模式: Factory Pattern + Singleton Pattern
- Factory: 根據名稱建立策略物件
- Singleton: 全域唯一的管理器實例
- Lazy Loading: 延遲載入策略物件，避免循環依賴

📁 設定檔位置: strategy_settings.json
"""

import json
import os
from typing import Optional, Dict, Any
from pathlib import Path


class StrategyManager:
    """策略管理器 - 負責策略載入與切換
    
    使用方式：
        manager = StrategyManager()
        strategy = manager.get_active_strategy()
        
        # 切換策略
        manager.set_active_strategy('v33_low_vol')
    """
    
    # Singleton 實例
    _instance: Optional['StrategyManager'] = None
    
    # 設定檔路徑
    SETTINGS_FILE = 'strategy_settings.json'
    
    # 預設設定
    DEFAULT_SETTINGS = {
        'active_strategy': 'v31_hybrid',
        'version': '1.0',
        'last_updated': None
    }
    
    # 策略註冊表（Lazy Loading，避免循環依賴）
    STRATEGY_REGISTRY = {
        'v31_hybrid': 'tool.strategies.v31_hybrid.V31HybridStrategy',
        'v33_low_vol': 'tool.strategies.v33_low_vol.V33LowVolStrategy',
        'v34_turbo': 'tool.strategies.v34_turbo.V34TurboStrategy',
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
        """載入設定檔"""
        try:
            with open(self.SETTINGS_FILE, 'r', encoding='utf-8') as f:
                settings = json.load(f)
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
    
    def get_active_strategy_name(self) -> str:
        """取得當前啟用的策略名稱
        
        Returns:
            str: 策略名稱，例如 'v31_hybrid'
        """
        settings = self.get_settings()
        return settings.get('active_strategy', 'v31_hybrid')
    
    def get_active_strategy(self):
        """取得當前啟用的策略物件
        
        Returns:
            BaseStrategy: 策略物件實例
        """
        strategy_name = self.get_active_strategy_name()
        
        # 檢查快取
        if strategy_name in self._strategy_cache:
            return self._strategy_cache[strategy_name]
        
        # 載入策略
        strategy = self._load_strategy(strategy_name)
        
        if strategy is None:
            # 載入失敗，回退到預設策略
            print(f"⚠️ 策略 '{strategy_name}' 載入失敗，回退到 'v31_hybrid'")
            strategy = self._load_strategy('v31_hybrid')
        
        # 快取策略物件
        self._strategy_cache[strategy_name] = strategy
        
        return strategy
    
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
    
    def set_active_strategy(self, strategy_name: str) -> bool:
        """切換當前啟用的策略
        
        Args:
            strategy_name: 要切換到的策略名稱
        
        Returns:
            bool: 成功返回 True，失敗返回 False
        """
        if strategy_name not in self.STRATEGY_REGISTRY:
            print(f"❌ 未知的策略: {strategy_name}")
            print(f"可用策略: {', '.join(self.STRATEGY_REGISTRY.keys())}")
            return False
        
        # 測試載入策略
        strategy = self._load_strategy(strategy_name)
        if strategy is None:
            return False
        
        # 更新設定
        settings = self.get_settings()
        settings['active_strategy'] = strategy_name
        self._save_settings(settings)
        
        # 更新快取
        self._settings = settings
        self._strategy_cache[strategy_name] = strategy
        
        print(f"🔄 策略已切換: {strategy.display_name}")
        return True
    
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
        self.set_active_strategy('v31_hybrid')
    
    def __repr__(self) -> str:
        strategy_name = self.get_active_strategy_name()
        return f"<StrategyManager: active={strategy_name}>"


# ============================================
# 全域便捷函數
# ============================================

def get_active_strategy():
    """全域便捷函數：取得當前策略
    
    Returns:
        BaseStrategy: 當前啟用的策略物件
    """
    manager = StrategyManager()
    return manager.get_active_strategy()


def get_strategy_manager() -> StrategyManager:
    """全域便捷函數：取得管理器實例
    
    Returns:
        StrategyManager: 管理器實例
    """
    return StrategyManager()
