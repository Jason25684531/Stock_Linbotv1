"""
V35 Refactor & Flex Message 測試
============================================
測試範圍:
1. Flex Message 建構器 (line_message_builder)
2. 策略出場邏輯解耦 (check_exit_signal)
3. 回測引擎共用出場方法 (check_and_execute_exit)
4. report_helper 資料聚合
5. strategy.py 清理後的向後相容性

作者: Stock AI Bot Team
最後更新: 2026-02-14
"""
import json
import pytest


# ============================================
# 1. Flex Message 建構器測試
# ============================================

class TestFlexMessageBuilder:
    """測試 tool/line_message_builder.py"""

    def test_create_stock_flex_message_full_data(self):
        """完整資料 → 產生正確的 Flex Bubble"""
        from tool.line_message_builder import create_stock_flex_message

        data = {
            'stock_id': '2330',
            'close_price': 850.0,
            'ma20': 840.0,
            'ma60': 820.0,
            'ma_trend': '多頭排列 📈',
            'rsi': 55.3,
            'ai_score': 0.78,
            'strategy_name': 'V35 經營效益',
            'op_margin': 0.152,
            'revenue_yoy': 20.5,
        }

        msg = create_stock_flex_message('2330', data)
        j = json.loads(msg.to_json())

        assert j['type'] == 'flex'
        assert '2330' in msg.alt_text
        assert j['contents']['type'] == 'bubble'
        assert j['contents']['header'] is not None
        assert j['contents']['hero'] is not None
        assert j['contents']['body'] is not None
        assert j['contents']['footer'] is not None

        # Footer 應包含 Goodinfo 連結
        footer_btn = j['contents']['footer']['contents'][0]
        assert 'goodinfo' in footer_btn['action']['uri'].lower()
        assert '2330' in footer_btn['action']['uri']

    def test_create_stock_flex_message_sparse_data(self):
        """缺失資料 → 不報錯, N/A 顯示"""
        from tool.line_message_builder import create_stock_flex_message

        sparse = {
            'stock_id': '9999',
            'close_price': 10.0,
            'ma20': None, 'ma60': None,
            'ma_trend': '無資料',
            'rsi': None,
            'ai_score': None,
            'strategy_name': None,
            'op_margin': None,
            'revenue_yoy': None,
        }

        msg = create_stock_flex_message('9999', sparse)
        j = json.loads(msg.to_json())

        assert j['contents']['type'] == 'bubble'
        assert '9999' in msg.alt_text

    def test_recommendation_carousel_includes_linbot_news_tag(self):
        from tool.line_message_builder import create_recommendation_carousel

        picks = [{
            'stock_id': '2330',
            'sector': '半導體',
            'close_price': 950.0,
            'ai_score': 0.81,
            'rsi': 58.5,
            'volume': 180000,
            'stop_loss_price': 874.0,
            'take_profit_price': 1092.5,
            'news_boost_reason': '半導體題材: 先進封裝需求增溫',
            'news_reason_items': ['半導體題材: 先進封裝需求增溫'],
            'news_is_bearish': False,
        }]

        msg = create_recommendation_carousel(
            picks=picks,
            strategy_name='V36 籌碼動能策略',
            date_str='2026-04-02',
        )
        payload = json.loads(msg.to_json())
        rendered = json.dumps(payload, ensure_ascii=False)

        assert payload['contents']['type'] == 'carousel'
        assert 'Linbot 利多' in rendered
        assert '先進封裝需求增溫' in rendered

    def test_flex_color_helpers(self):
        """顏色 helper 正確回傳"""
        from tool.line_message_builder import _color_by_value, _ai_score_label

        assert _color_by_value(10) == '#1DB446'  # positive → green
        assert _color_by_value(-5) == '#DD2222'  # negative → red
        assert _color_by_value(None) == '#888888' # None → neutral

        assert '🔥' in _ai_score_label(0.75)
        assert '👍' in _ai_score_label(0.55)
        assert '⚠️' in _ai_score_label(0.35)
        assert '尚無評分' in _ai_score_label(None)


# ============================================
# 2. 策略出場邏輯 (check_exit_signal) 測試
# ============================================

class TestCheckExitSignal:
    """測試 BaseStrategy.check_exit_signal() 邏輯"""

    def _get_strategy(self):
        from tool.strategy_manager import StrategyManager
        mgr = StrategyManager()
        return mgr._get_or_load_strategy('v31_hybrid')

    def test_trailing_stop_level1(self):
        """獲利 >= 10% → Level 1 保本停損"""
        strategy = self._get_strategy()
        position = {
            'cost': 100.0,
            'stop_loss': 93.0,
            'days': 3,
            'highest': 110.0,
        }

        action, reason, new_stop = strategy.check_exit_signal(
            '2330', 112.0, '2026-01-15', position, 'BULL'
        )

        # 獲利 12% → Level 1, stop 應上移到 101
        assert new_stop >= 101.0
        # 未觸及停損，應繼續持有
        assert action == 'HOLD'

    def test_trailing_stop_level3(self):
        """獲利 >= 30% → Level 3 鎖定利潤"""
        strategy = self._get_strategy()
        position = {
            'cost': 100.0,
            'stop_loss': 93.0,
            'days': 5,
            'highest': 131.0,
        }

        action, reason, new_stop = strategy.check_exit_signal(
            '2330', 131.0, '2026-01-20', position, 'BULL'
        )

        # 獲利 31% → Level 3, stop 應至少 125
        assert new_stop >= 125.0

    def test_stop_loss_trigger(self):
        """價格跌破停損 → SELL"""
        strategy = self._get_strategy()
        position = {
            'cost': 100.0,
            'stop_loss': 93.0,
            'days': 2,
            'highest': 100.0,
        }

        action, reason, new_stop = strategy.check_exit_signal(
            '2330', 92.0, '2026-01-10', position, 'BULL'
        )

        assert action == 'SELL'
        assert '停損' in reason

    def test_max_hold_days(self):
        """超過最長持有天數 → SELL"""
        strategy = self._get_strategy()
        position = {
            'cost': 100.0,
            'stop_loss': 93.0,
            'days': strategy.max_hold_days,
            'highest': 102.0,
        }

        action, reason, _ = strategy.check_exit_signal(
            '2330', 101.0, '2026-01-25', position, 'BULL'
        )

        assert action == 'SELL'
        assert '時間' in reason

    def test_bear_market_cut(self):
        """空頭且虧損 → SELL"""
        strategy = self._get_strategy()
        position = {
            'cost': 100.0,
            'stop_loss': 93.0,
            'days': 2,
            'highest': 100.0,
        }

        action, reason, _ = strategy.check_exit_signal(
            '2330', 98.0, '2026-01-12', position, 'BEAR'
        )

        assert action == 'SELL'
        assert '轉空' in reason


# ============================================
# 3. 策略模組清理後向後相容性測試
# ============================================

class TestStrategyBackwardCompat:
    """確認清理 strategy.py 後舊 import 路徑仍可用"""

    def test_get_v30_candidates_importable(self):
        from tool.strategy import get_v30_candidates
        assert callable(get_v30_candidates)

    def test_get_v30_params_from_db_importable(self):
        from tool.strategy import get_v30_params_from_db
        assert callable(get_v30_params_from_db)

    def test_calculate_v30_signal_importable(self):
        from tool.strategy import calculate_v30_signal
        assert callable(calculate_v30_signal)

    def test_format_functions_importable(self):
        from tool.strategy import (
            format_v30_recommendation,
            format_v31_recommendation,
            format_stock_query,
            format_strategy_message,
            calculate_pivot_strategy,
        )
        assert all(callable(f) for f in [
            format_v30_recommendation, format_v31_recommendation,
            format_stock_query, format_strategy_message, calculate_pivot_strategy
        ])

    def test_removed_functions_absent(self):
        """已移除的函式不應存在"""
        import tool.strategy as mod
        assert not hasattr(mod, 'check_sentiment_filter')
        assert not hasattr(mod, 'check_market_trend')
        assert not hasattr(mod, '_load_v31_model')
        assert not hasattr(mod, 'calculate_position_size')


# ============================================
# 4. report_helper 測試
# ============================================

class TestReportHelper:
    """測試 tool/report_helper.py (不需 DB)"""

    def test_format_stock_diagnosis_none(self):
        from tool.report_helper import format_stock_diagnosis
        result = format_stock_diagnosis(None)
        assert '查無' in result

    def test_format_stock_diagnosis_full(self):
        from tool.report_helper import format_stock_diagnosis
        report = {
            'stock_id': '2330',
            'close_price': 850.0,
            'ma_trend': '多頭排列 📈',
            'rsi': 55.0,
            'ai_score': 0.72,
            'strategy_name': 'V35',
            'op_margin': 0.15,
            'revenue_yoy': 20.0,
        }
        result = format_stock_diagnosis(report)
        assert '2330' in result
        assert '850' in result
        assert '多頭' in result
