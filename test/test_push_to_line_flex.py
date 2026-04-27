from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

from linebot.v3.messaging import FlexBox, FlexBubble, FlexCarousel, FlexText


def _fake_news_bubble() -> FlexBubble:
    return FlexBubble(size='mega', body=FlexBox(layout='vertical', contents=[]))


def _bubble_body_texts(bubble: FlexBubble) -> list[str]:
    body = getattr(bubble, 'body', None)
    if body is None:
        return []

    texts: list[str] = []
    for item in getattr(body, 'contents', []):
        if isinstance(item, FlexText):
            texts.append(item.text)
    return texts


class _FakeResult:
    def fetchall(self):
        return []


class _FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, *_args, **_kwargs):
        return _FakeResult()


class _FakeEngine:
    def connect(self):
        return _FakeConnection()


def test_build_morning_flex_uses_shared_news_bubble_builder():
    from jobs.push_to_line import _build_morning_flex

    fake_news_bubble = _fake_news_bubble()

    with patch('jobs.push_to_line.build_news_summary_bubble', return_value=fake_news_bubble) as mock_builder:
        result = _build_morning_flex(
            news_summary='📌 標題\n→ 影響\n📊 綜合研判\n偏多觀察',
            picks=[('🧪 經營效益', '2330', 600.0, 0.91)],
            market_status='🔴 多頭 (進攻)',
            date_str='2026-04-19',
            picks_title='多策略精選',
        )

    assert result is not None
    assert result.alt_text == '🌅 StockAI 早報 2026-04-19'
    assert isinstance(result.contents, FlexCarousel)
    assert len(result.contents.contents) == 3
    assert result.contents.contents[1].size == 'mega'
    assert [bubble.size for bubble in result.contents.contents] == ['mega', 'mega', 'mega']
    mock_builder.assert_called_once()


def test_build_morning_flex_keeps_three_cards_when_picks_missing():
    from jobs.push_to_line import MORNING_PICKS_EMPTY_STATE, _build_morning_flex

    with patch('jobs.push_to_line.build_news_summary_bubble', return_value=_fake_news_bubble()):
        result = _build_morning_flex(
            news_summary='',
            picks=[],
            market_status='⚪ 資料不足',
            date_str='2026-04-19',
            picks_title='多策略精選｜暫無新標的',
        )

    assert isinstance(result.contents, FlexCarousel)
    assert len(result.contents.contents) == 3
    assert MORNING_PICKS_EMPTY_STATE in _bubble_body_texts(result.contents.contents[2])


def test_build_evening_flex_uses_shared_news_bubble_builder():
    from jobs.push_to_line import _build_evening_flex

    fake_news_bubble = _fake_news_bubble()

    with patch('jobs.push_to_line.build_news_summary_bubble', return_value=fake_news_bubble) as mock_builder:
        result = _build_evening_flex(
            news_summary='📌 標題\n→ 影響\n📊 綜合研判\n偏多觀察',
            picks=[('🧪 經營效益', '2330', 600.0, 0.91)],
            date_str='2026-04-19',
            picks_title='多策略精選',
            market_status='🔴 多頭 (進攻)',
            market_bias=1.23,
            sentiment_summary='消息面偏多',
            strategy_summaries=['V35｜2330 $600 91%｜目標 12% / 20天'],
        )

    assert result is not None
    assert isinstance(result.contents, FlexCarousel)
    assert len(result.contents.contents) == 3
    assert result.contents.contents[1].size == 'mega'
    assert [bubble.size for bubble in result.contents.contents] == ['mega', 'mega', 'mega']
    mock_builder.assert_called_once()


def test_run_morning_broadcasts_carousel_even_without_picks():
    from jobs.push_to_line import MORNING_PICKS_EMPTY_STATE, run_morning

    fake_news_module = SimpleNamespace(
        get_morning_news_summary=lambda: '📌 今晨重點\n→ 測試摘要'
    )
    fake_manager = SimpleNamespace(
        get_active_strategy_names=lambda: ['v35_innovation']
    )

    with patch.dict(sys.modules, {'core.news_agent': fake_news_module}):
        with patch('jobs.push_to_line.get_db_engine', return_value=object()):
            with patch('jobs.push_to_line.get_pipeline_baseline_date', return_value='2026-04-19'):
                with patch('jobs.push_to_line.get_market_status', return_value=('⚪ 資料不足', 0)):
                    with patch('jobs.push_to_line.StrategyManager', return_value=fake_manager):
                        with patch('jobs.push_to_line._pick_featured_stocks', return_value=([], '多策略精選')):
                            with patch('jobs.push_to_line._broadcast_flex') as mock_broadcast:
                                run_morning()

    mock_broadcast.assert_called_once()
    flex_message = mock_broadcast.call_args.args[0]
    assert isinstance(flex_message.contents, FlexCarousel)
    assert len(flex_message.contents.contents) == 3
    assert MORNING_PICKS_EMPTY_STATE in _bubble_body_texts(flex_message.contents.contents[2])


def test_run_evening_broadcasts_uniform_carousel():
    from jobs.push_to_line import run_evening

    fake_news_module = SimpleNamespace(
        get_morning_news_summary=lambda: '📌 盤後重點\n→ 測試摘要'
    )
    fake_strategy = SimpleNamespace(
        name='v35_innovation',
        display_name='V35',
        target_return=12,
        look_ahead_days=20,
    )
    fake_manager = SimpleNamespace(
        get_active_strategies=lambda: [fake_strategy],
        get_active_strategy_names=lambda: ['v35_innovation'],
    )

    with patch.dict(sys.modules, {'core.news_agent': fake_news_module}):
        with patch('jobs.push_to_line.get_db_engine', return_value=_FakeEngine()):
            with patch('jobs.push_to_line.get_pipeline_baseline_date', return_value='2026-04-19'):
                with patch('jobs.push_to_line.get_market_status', return_value=('🔴 多頭 (進攻)', 1.23)):
                    with patch('jobs.push_to_line.StrategyManager', return_value=fake_manager):
                        with patch('jobs.push_to_line._pick_featured_stocks', return_value=([
                            ('🧪 經營效益', '2330', 600.0, 0.91),
                        ], '多策略精選')):
                            with patch('jobs.push_to_line._broadcast_text'):
                                with patch('jobs.push_to_line._broadcast_flex') as mock_flex:
                                    run_evening()

    mock_flex.assert_called_once()
    flex_message = mock_flex.call_args.args[0]
    assert isinstance(flex_message.contents, FlexCarousel)
    assert len(flex_message.contents.contents) == 3
    assert [bubble.size for bubble in flex_message.contents.contents] == ['mega', 'mega', 'mega']