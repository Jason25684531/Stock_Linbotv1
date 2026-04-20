"""
T015–T022: Rich Menu MCP 深度整合測試套件
============================================
涵蓋：
  - T015: TestPostbackCache       — TTL 快取邏輯
  - T016: TestMarketSummaryHandler — _build_market_summary_messages()
  - T017: TestChipTrendHandler     — _build_chip_trend_messages()
  - T018: TestRandomStrategyHandler — _build_random_strategy_messages()
  - T019: TestPostbackRouter       — dict-dispatch 路由
  - T020: TestStrategyManagerPool  — get_random_strategy_pool()
  - T021: TestRichMenuLayout       — build_default_rich_menu_request()
  - T022: TestPostbackFlow         — 端對端 postback handler 整合流程
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ──────────────────────────────────────────────
# T015: _PostbackCache
# ──────────────────────────────────────────────

class TestPostbackCache:
    """驗證 _PostbackCache 的 TTL、執行緒安全與空值拒絕行為。"""

    @pytest.fixture(autouse=True)
    def _fresh_cache(self):
        """每個測試使用獨立的 _PostbackCache 實例（不依賴模組 singleton）。"""
        # 直接匯入類別而非 singleton，以便隔離測試
        from app import _PostbackCache  # type: ignore[attr-defined]
        self.cache = _PostbackCache()
        self.cache._TTL_SECONDS = 1.0  # 縮短 TTL 供過期測試使用

    def test_set_and_get_returns_value(self):
        self.cache.set('market_summary', {'records': [{'a': 1}]})
        assert self.cache.get('market_summary') == {'records': [{'a': 1}]}

    def test_miss_returns_none(self):
        assert self.cache.get('nonexistent_action') is None

    def test_expired_entry_returns_none(self):
        self.cache._TTL_SECONDS = 0.05
        self.cache.set('market_summary', 'some data')
        time.sleep(0.1)
        assert self.cache.get('market_summary') is None

    def test_none_payload_not_stored(self):
        self.cache.set('chip_trend', None)
        assert self.cache.get('chip_trend') is None

    def test_empty_records_not_stored(self):
        self.cache.set('market_summary', {'records': []})
        assert self.cache.get('market_summary') is None

    def test_non_empty_records_stored(self):
        payload = {'records': [{'x': 1}, {'x': 2}]}
        self.cache.set('market_summary', payload)
        assert self.cache.get('market_summary') is not None

    def test_non_dict_payload_stored(self):
        """純字串 payload（已格式化好的訊息文字）也應可正常快取。"""
        self.cache.set('market_summary', '大盤資料文字')
        assert self.cache.get('market_summary') == '大盤資料文字'

    def test_thread_safe_concurrent_writes(self):
        """多執行緒並發寫入不應引發 RuntimeError。"""
        errors: list[Exception] = []

        def _write():
            try:
                for i in range(50):
                    self.cache.set(f'action_{i % 5}', f'data_{i}')
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_write) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == [], f"執行緒安全錯誤: {errors}"

    def test_cache_key_includes_date(self):
        """快取鍵應包含今日日期，確保跨日不命中舊資料。"""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        today = datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d')
        key = self.cache._make_key('market_summary')
        assert key == f'market_summary:{today}'


# ──────────────────────────────────────────────
# T016: _build_market_summary_messages
# ──────────────────────────────────────────────

class TestMarketSummaryHandler:
    """驗證 market_summary postback handler 的各種路徑。"""

    def _run(self):
        from app import _build_market_summary_messages  # type: ignore[attr-defined]
        return _build_market_summary_messages()

    @patch('app._postback_cache')
    @patch('app.MCPClient')
    def test_returns_text_message_list(self, mock_mcp_cls, mock_cache):
        mock_cache.get.return_value = None
        client = MagicMock()
        mock_mcp_cls.return_value = client
        records = [
            {'open_price': 10, 'close_price': 11, 'volume': 10000},
            {'open_price': 10, 'close_price': 9, 'volume': 5000},
            {'open_price': 10, 'close_price': 10, 'volume': 3000},
        ]
        client.get_market_statistics_sync.return_value = {
            'as_of_date': '2026-04-07',
            'records': records,
        }

        from linebot.v3.messaging import TextMessage as V3TextMessage
        result = self._run()
        assert len(result) == 1
        assert isinstance(result[0], V3TextMessage)
        assert '上漲     1 檔' in result[0].text
        assert '下跌     1 檔' in result[0].text
        assert '平盤     1 檔' in result[0].text

    @patch('app._postback_cache')
    @patch('app.MCPClient')
    def test_empty_records_returns_placeholder(self, mock_mcp_cls, mock_cache):
        mock_cache.get.return_value = None
        client = MagicMock()
        mock_mcp_cls.return_value = client
        client.get_market_statistics_sync.return_value = {'records': []}

        result = self._run()
        assert '尚無今日交易資料' in result[0].text

    @patch('app._postback_cache')
    def test_cache_hit_skips_mcp_call(self, mock_cache):
        mock_cache.get.return_value = '已快取的大盤資料'
        result = self._run()
        assert result[0].text == '已快取的大盤資料'

    @patch('app._postback_cache')
    @patch('app.MCPClient')
    def test_mcp_error_returns_error_message(self, mock_mcp_cls, mock_cache):
        from core.mcp_client import MCPClientError
        mock_cache.get.return_value = None
        err = MCPClientError(
            'connection refused',
            endpoint='/v1/stock-basic-snapshot',
            correlation_id='test-correlation-id',
        )
        mock_instance = MagicMock()
        mock_instance.get_market_statistics_sync.side_effect = err
        mock_mcp_cls.return_value = mock_instance

        result = self._run()
        assert '無法連線' in result[0].text or '暫時' in result[0].text


# ──────────────────────────────────────────────
# T017: _build_chip_trend_messages
# ──────────────────────────────────────────────

class TestChipTrendHandler:
    """驗證 chip_trend postback handler 的各種路徑。"""

    def _run(self):
        from app import _build_chip_trend_messages  # type: ignore[attr-defined]
        return _build_chip_trend_messages()

    @patch('app._postback_cache')
    @patch('app.MCPClient')
    def test_aggregates_three_investors(self, mock_mcp_cls, mock_cache):
        mock_cache.get.return_value = None
        client = MagicMock()
        mock_mcp_cls.return_value = client
        records = [
            {
                'foreign_buy': 1000,
                'trust_buy': 500,
                'dealer_buy': -200,
            }
        ]
        client.get_foreign_investment_sync.return_value = {
            'as_of_date': '2026-04-07',
            'records': records,
        }

        from linebot.v3.messaging import TextMessage as V3TextMessage
        result = self._run()
        assert isinstance(result[0], V3TextMessage)
        assert '1,000' in result[0].text or '外資' in result[0].text

    @patch('app._postback_cache')
    @patch('app.MCPClient')
    def test_empty_records_returns_placeholder(self, mock_mcp_cls, mock_cache):
        mock_cache.get.return_value = None
        client = MagicMock()
        mock_mcp_cls.return_value = client
        client.get_foreign_investment_sync.return_value = {'records': []}

        result = self._run()
        assert '尚無今日法人資料' in result[0].text

    @patch('app._postback_cache')
    def test_cache_hit_skips_mcp_call(self, mock_cache):
        mock_cache.get.return_value = '已快取的籌碼資料'
        result = self._run()
        assert result[0].text == '已快取的籌碼資料'


# ──────────────────────────────────────────────
# T018: _build_random_strategy_messages
# ──────────────────────────────────────────────

class TestRandomStrategyHandler:
    """驗證 random_strategy postback handler 的策略輪詢與 fallback 邏輯。"""

    def _run(self):
        from app import _build_random_strategy_messages  # type: ignore[attr-defined]
        return _build_random_strategy_messages()

    @patch('app.get_stock_data')
    @patch('app.strategy_manager')
    def test_returns_recommendation_on_success(self, mock_sm, mock_gsd):
        mock_sm.get_random_strategy_pool.return_value = ['v35_innovation']
        strategy = MagicMock()
        strategy.display_name = 'V35 創新策略'
        candidates = pd.DataFrame({
            'stock_id': ['2330'],
            'close_price': [600.0],
            'stock_name': ['台積電'],
        })
        strategy.filter_candidates.return_value = candidates
        mock_sm.get_strategy.return_value = strategy
        mock_gsd.return_value = (pd.DataFrame({'stock_id': ['2330']}), '2026-04-02')

        from linebot.v3.messaging import TextMessage as V3TextMessage
        result = self._run()
        assert isinstance(result[0], V3TextMessage)
        assert 'V35' in result[0].text or '策略盲盒' in result[0].text

    @patch('app.get_stock_data')
    @patch('app.strategy_manager')
    def test_empty_pool_returns_placeholder(self, mock_sm, mock_gsd):
        mock_sm.get_random_strategy_pool.return_value = []
        result = self._run()
        assert '策略池為空' in result[0].text

    @patch('app.get_stock_data')
    @patch('app.strategy_manager')
    def test_all_strategies_empty_returns_no_candidate_message(self, mock_sm, mock_gsd):
        mock_sm.get_random_strategy_pool.return_value = ['v35_innovation']
        strategy = MagicMock()
        strategy.filter_candidates.return_value = pd.DataFrame()
        mock_sm.get_strategy.return_value = strategy
        mock_gsd.return_value = (pd.DataFrame({'stock_id': ['2330']}), '2026-04-02')

        result = self._run()
        assert '均無符合條件' in result[0].text or '策略盲盒' in result[0].text

    @patch('app.get_stock_data')
    @patch('app.strategy_manager')
    def test_no_market_data_returns_placeholder(self, mock_sm, mock_gsd):
        mock_sm.get_random_strategy_pool.return_value = ['v35_innovation']
        mock_gsd.return_value = (pd.DataFrame(), None)

        result = self._run()
        assert '無法取得市場資料' in result[0].text


# ──────────────────────────────────────────────
# T019: Postback 路由 dict-dispatch
# ──────────────────────────────────────────────

class TestPostbackRouter:
    """驗證 _build_postback_reply_messages 的路由分派行為。"""

    def test_macro_summary_routes_correctly(self):
        with patch('app._build_macro_news_messages') as mock_handler:
            mock_handler.return_value = ['msg']
            from app import _build_postback_reply_messages  # type: ignore[attr-defined]
            result = _build_postback_reply_messages('macro_summary')
            mock_handler.assert_called_once()
            assert result == ['msg']

    def test_journal_reflection_routes_correctly(self):
        with patch('app._build_journal_reflection_messages') as mock_handler:
            mock_handler.return_value = ['msg']
            from app import _build_postback_reply_messages  # type: ignore[attr-defined]
            result = _build_postback_reply_messages('journal_reflection')
            mock_handler.assert_called_once()
            assert result == ['msg']

    def test_choose_strategy_routes_correctly(self):
        with patch('app._build_strategy_picker_messages') as mock_handler:
            mock_handler.return_value = ['msg']
            from app import _build_postback_reply_messages  # type: ignore[attr-defined]
            result = _build_postback_reply_messages('choose_strategy')
            mock_handler.assert_called_once()
            assert result == ['msg']

    def test_select_strategy_routes_with_payload(self):
        with patch('app._build_selected_strategy_messages') as mock_handler:
            mock_handler.return_value = ['msg']
            from app import _build_postback_reply_messages  # type: ignore[attr-defined]
            result = _build_postback_reply_messages('select_strategy', payload={'strategy': 'v35_innovation'})
            mock_handler.assert_called_once_with({'strategy': 'v35_innovation'})
            assert result == ['msg']

    def test_strategy_select_routes_with_payload(self):
        with patch('app._build_selected_strategy_messages') as mock_handler:
            mock_handler.return_value = ['msg']
            from app import _build_postback_reply_messages  # type: ignore[attr-defined]
            result = _build_postback_reply_messages('strategy_select', payload={'strategy': 'v35'})
            mock_handler.assert_called_once_with({'strategy': 'v35'})
            assert result == ['msg']

    def test_backtest_reflect_routes_with_payload(self):
        with patch('app._build_backtest_reflection_messages') as mock_handler:
            mock_handler.return_value = ['msg']
            from app import _build_postback_reply_messages  # type: ignore[attr-defined]
            result = _build_postback_reply_messages('backtest_reflect', payload={'strategy': 'v31'})
            mock_handler.assert_called_once_with({'strategy': 'v31'})
            assert result == ['msg']

    def test_market_summary_routes_correctly(self):
        with patch('app._build_market_summary_messages') as mock_handler:
            mock_handler.return_value = ['msg']
            from app import _build_postback_reply_messages  # type: ignore[attr-defined]
            result = _build_postback_reply_messages('market_summary')
            mock_handler.assert_called_once()
            assert result == ['msg']

    def test_chip_trend_routes_correctly(self):
        with patch('app._build_chip_trend_messages') as mock_handler:
            mock_handler.return_value = ['msg']
            from app import _build_postback_reply_messages  # type: ignore[attr-defined]
            result = _build_postback_reply_messages('chip_trend')
            mock_handler.assert_called_once()
            assert result == ['msg']

    def test_random_strategy_routes_correctly(self):
        with patch('app._build_random_strategy_messages') as mock_handler:
            mock_handler.return_value = ['msg']
            from app import _build_postback_reply_messages  # type: ignore[attr-defined]
            result = _build_postback_reply_messages('random_strategy')
            mock_handler.assert_called_once()
            assert result == ['msg']

    def test_get_macro_news_still_works(self):
        with patch('app._build_macro_news_messages') as mock_handler:
            mock_handler.return_value = ['news_msg']
            from app import _build_postback_reply_messages  # type: ignore[attr-defined]
            result = _build_postback_reply_messages('get_macro_news')
            mock_handler.assert_called_once()
            assert result == ['news_msg']

    def test_unknown_action_returns_unsupported(self):
        from linebot.v3.messaging import FlexMessage
        from app import _build_postback_reply_messages  # type: ignore[attr-defined]
        result = _build_postback_reply_messages('this_action_does_not_exist')
        assert len(result) == 1
        assert isinstance(result[0], FlexMessage)
        rendered = json.dumps(json.loads(result[0].to_json()), ensure_ascii=False)
        assert '尚未支援' in rendered


# ──────────────────────────────────────────────
# T020: StrategyManager.get_random_strategy_pool
# ──────────────────────────────────────────────

class TestStrategyManagerPool:
    """驗證 StrategyManager.get_random_strategy_pool() 的各種讀取路徑。"""

    @pytest.fixture(autouse=True)
    def _reset_singleton(self):
        from core.strategy_manager import StrategyManager
        StrategyManager._instance = None

    def test_returns_list_of_valid_keys(self):
        from core.strategy_manager import StrategyManager
        sm = StrategyManager()
        pool = sm.get_random_strategy_pool()
        assert isinstance(pool, list)
        for key in pool:
            assert key in sm.STRATEGY_REGISTRY

    def test_invalid_keys_are_filtered(self):
        from core.strategy_manager import StrategyManager
        sm = StrategyManager()
        fake_settings = {'random_strategy_pool': ['v35_innovation', 'nonexistent_strategy_xyz']}
        with patch.object(sm, 'get_settings', return_value=fake_settings):
            pool = sm.get_random_strategy_pool()
        assert 'nonexistent_strategy_xyz' not in pool
        assert 'v35_innovation' in pool

    def test_non_list_value_falls_back_to_default(self):
        from core.strategy_manager import StrategyManager
        sm = StrategyManager()
        fake_settings = {'random_strategy_pool': 'not_a_list'}
        with patch.object(sm, 'get_settings', return_value=fake_settings):
            pool = sm.get_random_strategy_pool()
        assert isinstance(pool, list)

    def test_missing_key_uses_default(self):
        from core.strategy_manager import StrategyManager
        sm = StrategyManager()
        with patch.object(sm, 'get_settings', return_value={}):
            pool = sm.get_random_strategy_pool()
        assert isinstance(pool, list)
        assert len(pool) > 0


# ──────────────────────────────────────────────
# T021: Rich Menu 版面配置
# ──────────────────────────────────────────────

class TestRichMenuLayout:
    """驗證 build_default_rich_menu_request() 產生正確的 4 按鈕版面。"""

    def test_has_four_areas(self):
        from core.richmenu import build_default_rich_menu_request
        req = build_default_rich_menu_request()
        assert len(req.areas) == 4, f"預期 4 個區域，實際有 {len(req.areas)} 個"

    def test_macro_summary_area_exists(self):
        from core.richmenu import build_default_rich_menu_request
        from linebot.v3.messaging import PostbackAction
        req = build_default_rich_menu_request()
        postback_data = [
            area.action.data
            for area in req.areas
            if isinstance(area.action, PostbackAction) and area.action.data
        ]
        assert 'action=macro_summary' in postback_data, \
            f"找不到 macro_summary postback action，現有: {postback_data}"

    def test_journal_reflection_area_exists(self):
        from core.richmenu import build_default_rich_menu_request
        from linebot.v3.messaging import PostbackAction
        req = build_default_rich_menu_request()
        postback_data = [
            area.action.data
            for area in req.areas
            if isinstance(area.action, PostbackAction) and area.action.data
        ]
        assert 'action=journal_reflection' in postback_data, \
            f"找不到 journal_reflection postback action，現有: {postback_data}"

    def test_choose_strategy_area_exists(self):
        from core.richmenu import build_default_rich_menu_request
        from linebot.v3.messaging import PostbackAction
        req = build_default_rich_menu_request()
        postback_data = [
            area.action.data
            for area in req.areas
            if isinstance(area.action, PostbackAction) and area.action.data
        ]
        assert 'action=choose_strategy' in postback_data, \
            f"找不到 choose_strategy postback action，現有: {postback_data}"

    def test_stock_diagnosis_postback_exists(self):
        """左上角「個股診斷」應使用 PostbackAction 引導輸入。"""
        from core.richmenu import build_default_rich_menu_request
        from linebot.v3.messaging import PostbackAction
        req = build_default_rich_menu_request()
        postback_data = [
            area.action.data
            for area in req.areas
            if isinstance(area.action, PostbackAction) and area.action.data
        ]
        assert 'action=prompt_stock_diagnosis' in postback_data, \
            f"找不到 prompt_stock_diagnosis postback action，現有: {postback_data}"


class TestRichMenuSync:
    """驗證 Rich Menu 同步流程仍由唯一工具模組負責。"""

    def test_sync_default_rich_menu_uploads_image_and_sets_default(self, tmp_path):
        from core.richmenu import sync_default_rich_menu

        image_path = tmp_path / 'richmenu.png'
        image_path.write_bytes(b'png')
        calls = {}

        class FakeMessagingApi:
            def create_rich_menu(self, rich_menu_request):
                calls['request'] = json.loads(rich_menu_request.to_json())
                return SimpleNamespace(rich_menu_id='richmenu-123')

            def set_default_rich_menu(self, rich_menu_id):
                calls['default_id'] = rich_menu_id

        def fake_upload_rich_menu_image(rich_menu_id, channel_access_token, image_path=None, timeout=30):
            calls['image_id'] = rich_menu_id
            calls['token'] = channel_access_token
            calls['image_body'] = image_path
            calls['timeout'] = timeout

        import core.richmenu as richmenu_module

        original_uploader = richmenu_module.upload_rich_menu_image
        richmenu_module.upload_rich_menu_image = fake_upload_rich_menu_image
        try:
            rich_menu_id = sync_default_rich_menu(
                messaging_api=FakeMessagingApi(),
                channel_access_token='token-123',
                image_path=image_path,
            )
        finally:
            richmenu_module.upload_rich_menu_image = original_uploader

        assert rich_menu_id == 'richmenu-123'
        assert calls['default_id'] == 'richmenu-123'
        assert calls['image_id'] == 'richmenu-123'
        assert Path(calls['image_body']) == image_path.resolve()
        assert calls['token'] == 'token-123'


# ──────────────────────────────────────────────
# T022: 端對端 Postback 整合流程
# ──────────────────────────────────────────────

class TestPostbackFlow:
    """驗證 _extract_postback_action + _build_postback_reply_messages 的端對端流程。"""

    def test_extract_action_from_standard_payload(self):
        from app import _extract_postback_action  # type: ignore[attr-defined]
        assert _extract_postback_action('action=prompt_stock_diagnosis') == 'prompt_stock_diagnosis'
        assert _extract_postback_action('action=macro_summary') == 'macro_summary'
        assert _extract_postback_action('action=journal_reflection') == 'journal_reflection'
        assert _extract_postback_action('action=choose_strategy') == 'choose_strategy'

    def test_extract_action_with_extra_query_params(self):
        from app import _extract_postback_action  # type: ignore[attr-defined]
        assert _extract_postback_action('action=select_strategy&strategy=v35_innovation') == 'select_strategy'
        assert _extract_postback_action('action=strategy_select&strategy=v35') == 'strategy_select'
        assert _extract_postback_action('action=backtest_reflect&strategy=v31') == 'backtest_reflect'

    def test_extract_action_empty_string(self):
        from app import _extract_postback_action  # type: ignore[attr-defined]
        assert _extract_postback_action('') == ''
        assert _extract_postback_action(None) == ''  # type: ignore[arg-type]

    def test_full_flow_market_summary(self):
        with patch('app._build_market_summary_messages') as mock_handler:
            mock_handler.return_value = [MagicMock(text='大盤資料')]
            from app import _extract_postback_action, _build_postback_reply_messages  # type: ignore
            action = _extract_postback_action('action=market_summary')
            result = _build_postback_reply_messages(action)
            assert len(result) == 1
            mock_handler.assert_called_once()

    def test_full_flow_chip_trend(self):
        with patch('app._build_chip_trend_messages') as mock_handler:
            mock_handler.return_value = [MagicMock(text='籌碼資料')]
            from app import _extract_postback_action, _build_postback_reply_messages  # type: ignore
            action = _extract_postback_action('action=chip_trend')
            result = _build_postback_reply_messages(action)
            assert len(result) == 1
            mock_handler.assert_called_once()

    def test_full_flow_random_strategy(self):
        with patch('app._build_random_strategy_messages') as mock_handler:
            mock_handler.return_value = [MagicMock(text='盲盒結果')]
            from app import _extract_postback_action, _build_postback_reply_messages  # type: ignore
            action = _extract_postback_action('action=random_strategy')
            result = _build_postback_reply_messages(action)
            assert len(result) == 1
            mock_handler.assert_called_once()

    def test_backward_compat_get_journal(self):
        with patch('app._build_journal_reflection_messages') as mock_journal:
            mock_journal.return_value = ['日誌反思訊息']
            from app import _build_postback_reply_messages  # type: ignore
            result = _build_postback_reply_messages('get_journal')
            assert result == ['日誌反思訊息']

    def test_postback_handler_routes_reply_message(self, monkeypatch):
        import app as app_module

        sent = {}

        class DummyApiClient:
            def __init__(self, _configuration):
                self._configuration = _configuration

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class DummyMessagingApi:
            def __init__(self, _api_client):
                self._api_client = _api_client

            def reply_message(self, payload):
                sent.update(payload)

        monkeypatch.setattr(app_module, 'ApiClient', DummyApiClient)
        monkeypatch.setattr(app_module, 'MessagingApi', DummyMessagingApi)
        monkeypatch.setattr(app_module, 'ReplyMessageRequest', lambda **kwargs: kwargs)
        monkeypatch.setattr(
            app_module,
            '_build_postback_reply_messages',
            lambda action, payload=None, source_id='': [f'reply:{action}:{payload.get("strategy") if payload else ""}:{source_id}'],
        )

        event = SimpleNamespace(
            reply_token='reply-token-1',
            postback=SimpleNamespace(data='action=select_strategy&strategy=v35_innovation'),
            source=SimpleNamespace(user_id='user-1'),
        )

        app_module.postback_handler(event)

        assert sent['reply_token'] == 'reply-token-1'
        assert sent['messages'] == ['reply:select_strategy:v35_innovation:user-1']
