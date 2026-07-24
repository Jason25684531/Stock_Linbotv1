"""Characterize LINE command replies before registry extraction."""

from types import SimpleNamespace

import pytest


def _event(text):
    return SimpleNamespace(
        reply_token='baseline-token',
        source=SimpleNamespace(user_id='baseline-user'),
        message=SimpleNamespace(text=text),
    )


def _dispatch(monkeypatch, text):
    import app as app_module
    import app.line_bot as line_bot

    replies, updates = [], []
    monkeypatch.setattr(
        line_bot,
        'reply_message',
        lambda _token, messages: replies.extend(messages if isinstance(messages, list) else [messages]),
    )
    monkeypatch.setattr(app_module, 'update_setting', lambda key, value: updates.append((key, value)) or True)
    line_bot.handle_message(_event(text))
    return replies[0].text, updates


@pytest.mark.parametrize(
    ('command', 'expected_reply', 'expected_updates'),
    [
        ('設定V34 18 0.93 0.9', '✅ V34 嚴格參數已更新\nYoY>18.0% / 突破>=0.93 / 量比>0.90', [('v34_revenue_yoy_min', '18.0'), ('v34_breakout_ratio', '0.93'), ('v34_volume_ratio_min', '0.9')]),
        ('設定V34放寬 10 0.90 0.7', '✅ V34 放寬參數已更新\nYoY>10.0% / 突破>=0.90 / 量比>0.70', [('v34_relaxed_revenue_yoy_min', '10.0'), ('v34_relaxed_breakout_ratio', '0.9'), ('v34_relaxed_volume_ratio_min', '0.7')]),
        ('設定V35 6 0 0.8', '✅ V35 嚴格參數已更新\n利益率>6.0% / YoY>0.0% / 量比>0.80', [('v35_op_margin_min', '0.06'), ('v35_revenue_yoy_min', '0.0'), ('v35_volume_ratio_min', '0.8')]),
        ('設定V35放寬 4 -5 0.6', '✅ V35 放寬參數已更新\n利益率>4.0% / YoY>-5.0% / 量比>0.60', [('v35_relaxed_op_margin_min', '0.04'), ('v35_relaxed_revenue_yoy_min', '-5.0'), ('v35_relaxed_volume_ratio_min', '0.6')]),
    ],
)
def test_v34_v35_valid_parameter_baseline(monkeypatch, command, expected_reply, expected_updates):
    reply, updates = _dispatch(monkeypatch, command)
    assert reply == expected_reply
    assert updates == expected_updates


@pytest.mark.parametrize('command, expected_reply', [
    ('設定V34 301 0.93 0.9', '❌ 參數超出範圍\nYoY: -50~300, 突破: 0.70~1.10, 量比: 0~5'),
    ('設定V34放寬 10 1.11 0.7', '❌ 參數超出範圍\nYoY: -50~300, 突破: 0.70~1.10, 量比: 0~5'),
    ('設定V35 101 0 0.8', '❌ 參數超出範圍\n利益率%: -20~100, YoY: -100~300, 量比: 0~5'),
    ('設定V35放寬 4 -101 0.6', '❌ 參數超出範圍\n利益率%: -20~100, YoY: -100~300, 量比: 0~5'),
])
def test_v34_v35_out_of_range_parameter_baseline(monkeypatch, command, expected_reply):
    reply, updates = _dispatch(monkeypatch, command)
    assert reply == expected_reply
    assert updates == []


@pytest.mark.parametrize('command, expected_reply', [
    ('設定V34 1..2 0.93 0.9', '❌ 格式錯誤\n用法：設定V34 18 0.93 0.9'),
    ('設定V34放寬 1..2 0.90 0.7', '❌ 格式錯誤\n用法：設定V34放寬 10 0.90 0.7'),
    ('設定V35 1..2 0 0.8', '❌ 格式錯誤\n用法：設定V35 6 0 0.8'),
    ('設定V35放寬 1..2 -5 0.6', '❌ 格式錯誤\n用法：設定V35放寬 4 -5 0.6'),
])
def test_v34_v35_malformed_parameter_baseline(monkeypatch, command, expected_reply):
    reply, updates = _dispatch(monkeypatch, command)
    assert reply == expected_reply
    assert updates == []


def test_unrecognized_message_fallback_help_baseline(monkeypatch):
    reply, updates = _dispatch(monkeypatch, '不支援的指令')
    assert updates == []
    assert reply.startswith('🤖 【StockAI Line Bot V3.0】\n')
    assert '• 設定V35放寬 4 -5 0.6' in reply
    assert reply.endswith('⚠️ 所有推薦僅供參考')
