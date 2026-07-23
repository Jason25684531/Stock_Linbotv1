from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _reset_line_interaction_state():
    import app as app_module

    app_module._line_interaction_state.clear('user-1')
    yield
    app_module._line_interaction_state.clear('user-1')


def test_handle_message_diagnose_command_sets_pending_state(monkeypatch):
    import app as app_module
    import app.line_bot as line_bot_module

    sent = {}
    monkeypatch.setattr(
        line_bot_module,
        'reply_message',
        lambda reply_token, messages: sent.update({'reply_token': reply_token, 'messages': messages}),
    )

    event = SimpleNamespace(
        reply_token='reply-token-1',
        source=SimpleNamespace(user_id='user-1'),
        message=SimpleNamespace(text='診斷'),
    )

    line_bot_module.handle_message(event)

    assert sent['reply_token'] == 'reply-token-1'
    assert '4 碼股票代號' in sent['messages'][0].text
    assert app_module._line_interaction_state.get('user-1') == {'action': 'stock_diagnosis'}


def test_handle_message_consumes_pending_diagnosis(monkeypatch):
    import app as app_module
    import app.line_bot as line_bot_module

    app_module._line_interaction_state.set('user-1', {'action': 'stock_diagnosis'})
    captured = {}
    monkeypatch.setattr(
        line_bot_module,
        '_reply_stock_diagnosis',
        lambda reply_token, stock_id: captured.update({'reply_token': reply_token, 'stock_id': stock_id}) or True,
    )

    event = SimpleNamespace(
        reply_token='reply-token-2',
        source=SimpleNamespace(user_id='user-1'),
        message=SimpleNamespace(text='2330'),
    )

    line_bot_module.handle_message(event)

    assert captured == {'reply_token': 'reply-token-2', 'stock_id': '2330'}
    assert app_module._line_interaction_state.get('user-1') is None


def test_build_strategy_picker_messages_returns_flex_prompt(monkeypatch):
    import app as app_module
    from linebot.v3.messaging import FlexMessage

    monkeypatch.setattr(
        app_module,
        '_list_strategy_picker_options',
        lambda: [
            {
                'key': 'v35_innovation',
                'label': 'V35 經營效益策略',
                'short_label': 'V35',
                'payload_key': 'v35',
                'display_text': '查看 V35 經營效益策略',
            }
        ],
    )

    result = app_module._build_strategy_picker_messages()

    assert len(result) == 1
    assert isinstance(result[0], FlexMessage)
    payload = result[0].to_json()
    assert 'action=strategy_select&strategy=v35' in payload
    assert '策略選股' in result[0].alt_text


def test_build_selected_strategy_messages_targets_requested_strategy(monkeypatch):
    import app as app_module

    captured = {}
    marker = object()

    def fake_get_strategy_recommendation(as_flex=False, strategy_key=None):
        captured['as_flex'] = as_flex
        captured['strategy_key'] = strategy_key
        return marker

    monkeypatch.setattr(app_module, 'get_strategy_recommendation', fake_get_strategy_recommendation)

    result = app_module._build_selected_strategy_messages({'strategy': 'v35'})

    assert captured == {'as_flex': True, 'strategy_key': 'v35_innovation'}
    assert result == [marker]


def test_build_journal_reflection_messages_returns_flex(monkeypatch):
    import app as app_module
    from linebot.v3.messaging import FlexMessage

    monkeypatch.setattr(
        app_module,
        '_list_strategy_picker_options',
        lambda: [
            {
                'key': 'v35_innovation',
                'label': 'V35 經營效益策略',
                'short_label': 'V35',
                'payload_key': 'v35',
                'display_text': '查看 V35 經營效益策略',
            }
        ],
    )

    result = app_module._build_journal_reflection_messages()

    assert len(result) == 1
    assert isinstance(result[0], FlexMessage)
    assert '日誌反思' in result[0].alt_text
    assert 'action=backtest_reflect&strategy=v35' in result[0].to_json()


def test_build_backtest_reflection_messages_returns_flex(monkeypatch):
    import app as app_module
    from linebot.v3.messaging import FlexMessage

    monkeypatch.setattr(
        app_module,
        '_build_strategy_backtest_snapshot',
        lambda strategy_key: {
            'has_data': True,
            'strategy_key': 'v35_innovation',
            'strategy_name': 'V35 經營效益策略',
            'date_str': '2026-04-19',
            'source_label': '資料來源: 回測資料庫',
            'total_roi': 18.4,
            'win_rate': 62.5,
            'max_drawdown': -6.8,
            'trade_count': 16,
            'avg_hold_days': 5.2,
            'latest_trade_summary': '2330 +5.2%｜出場原因：停利',
        },
    )
    monkeypatch.setattr(
        app_module,
        '_build_strategy_reflection_suggestions',
        lambda snapshot: ['維持強勢族群觀察', '留意回撤控制'],
    )

    result = app_module._build_backtest_reflection_messages({'strategy': 'v35'})

    assert len(result) == 1
    assert isinstance(result[0], FlexMessage)
    rendered = json.dumps(json.loads(result[0].to_json()), ensure_ascii=False)
    assert 'V35 經營效益策略' in rendered
    assert '維持強勢族群觀察' in rendered


def test_build_backtest_reflection_messages_returns_empty_state_when_no_data(monkeypatch):
    import app as app_module
    from linebot.v3.messaging import FlexMessage

    monkeypatch.setattr(
        app_module,
        '_build_strategy_backtest_snapshot',
        lambda strategy_key: {
            'has_data': False,
            'strategy_key': 'v37_mean_reversion',
            'strategy_name': 'V37 均值回歸策略',
            'date_str': '2026-04-19',
        },
    )

    result = app_module._build_backtest_reflection_messages({'strategy': 'v37'})

    assert len(result) == 1
    assert isinstance(result[0], FlexMessage)
    rendered = json.dumps(json.loads(result[0].to_json()), ensure_ascii=False)
    assert '尚無該策略回測資料' in rendered


def test_build_selected_strategy_messages_returns_empty_state_on_missing_strategy():
    import app as app_module
    from linebot.v3.messaging import FlexMessage

    result = app_module._build_selected_strategy_messages({})

    assert len(result) == 1
    assert isinstance(result[0], FlexMessage)
    rendered = json.dumps(json.loads(result[0].to_json()), ensure_ascii=False)
    assert '缺少策略代號' in rendered