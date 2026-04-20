from __future__ import annotations

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


def test_build_strategy_picker_messages_returns_quick_reply(monkeypatch):
    import app as app_module
    from linebot.v3.messaging import TextMessage as V3TextMessage

    monkeypatch.setattr(
        app_module,
        '_list_strategy_picker_options',
        lambda: [
            {
                'key': 'v35_innovation',
                'label': 'V35 經營效益策略',
                'short_label': 'V35',
                'display_text': '查看 V35 經營效益策略',
            }
        ],
    )

    result = app_module._build_strategy_picker_messages()

    assert len(result) == 1
    assert isinstance(result[0], V3TextMessage)
    assert result[0].quick_reply is not None
    assert result[0].quick_reply.items[0].action.data == 'action=select_strategy&strategy=v35_innovation'


def test_build_selected_strategy_messages_targets_requested_strategy(monkeypatch):
    import app as app_module

    captured = {}
    marker = object()

    def fake_get_strategy_recommendation(as_flex=False, strategy_key=None):
        captured['as_flex'] = as_flex
        captured['strategy_key'] = strategy_key
        return marker

    monkeypatch.setattr(app_module, 'get_strategy_recommendation', fake_get_strategy_recommendation)

    result = app_module._build_selected_strategy_messages({'strategy': 'v35_innovation'})

    assert captured == {'as_flex': True, 'strategy_key': 'v35_innovation'}
    assert result == [marker]


def test_build_journal_reflection_messages_returns_flex(monkeypatch):
    import app as app_module
    from linebot.v3.messaging import FlexMessage

    monkeypatch.setattr(
        app_module,
        '_build_journal_reflection_snapshot',
        lambda: {
            'active_labels': ['V35 經營效益策略'],
            'date_str': '2026-04-19',
            'today_pick_status': '有標的（V35 經營效益策略）',
            'summary': {
                'total_roi': 18.4,
                'win_rate': 62.5,
                'trade_count': 16,
            },
            'latest_trade_summary': '2330 +5.2%｜出場原因：停利',
        },
    )

    result = app_module._build_journal_reflection_messages()

    assert len(result) == 1
    assert isinstance(result[0], FlexMessage)
    assert '日誌反思' in result[0].alt_text