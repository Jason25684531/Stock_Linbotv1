from __future__ import annotations

from unittest.mock import patch

from linebot.v3.messaging import FlexBox, FlexBubble


def _fake_news_bubble() -> FlexBubble:
    return FlexBubble(body=FlexBox(layout='vertical', contents=[]))


def test_build_morning_flex_uses_shared_news_bubble_builder():
    from jobs.push_to_line import _build_morning_flex

    with patch('jobs.push_to_line.build_news_summary_bubble', return_value=_fake_news_bubble()) as mock_builder:
        result = _build_morning_flex(
            news_summary='📌 標題\n→ 影響\n📊 綜合研判\n偏多觀察',
            picks=[('🧪 經營效益', '2330', 600.0, 0.91)],
            market_status='🔴 多頭 (進攻)',
            date_str='2026-04-19',
            picks_title='多策略精選',
        )

    assert result is not None
    mock_builder.assert_called_once()


def test_build_evening_flex_uses_shared_news_bubble_builder():
    from jobs.push_to_line import _build_evening_flex

    with patch('jobs.push_to_line.build_news_summary_bubble', return_value=_fake_news_bubble()) as mock_builder:
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
    mock_builder.assert_called_once()