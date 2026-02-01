"""
策略模組 - Strategy Factory Pattern
============================================
提供多策略切換功能

可用策略：
- V31 Hybrid: 均線 + RSI + 籌碼面 + ML 智慧排名
- V33 Low Vol: 低波動穩健型
- V34 Twin-Turbo: 雙渦輪飆股型
- V35 Innovation: 研發動能型 (基本面驅動)
"""

from .base import BaseStrategy
from .v31_hybrid import V31HybridStrategy
from .v33_low_vol import V33LowVolStrategy
from .v34_turbo import V34TurboStrategy
from .v35_innovation import V35InnovationStrategy

__all__ = [
    'BaseStrategy', 
    'V31HybridStrategy', 
    'V33LowVolStrategy', 
    'V34TurboStrategy',
    'V35InnovationStrategy'
]
