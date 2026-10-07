"""Canonical LINE Bot webhook entrypoint and message handlers."""

from __future__ import annotations

import json
import re
import traceback

from flask import abort, request
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage as V3TextMessage,
)
from linebot.v3.webhooks import MessageEvent, PostbackEvent, TextMessageContent

from . import app, handler, services
from . import line_commands


def reply_message(reply_token: str, messages) -> None:
    """統一 LINE reply path，供 callback / postback / message handler 共用。"""
    normalized = messages if isinstance(messages, list) else [messages]
    with ApiClient(services.configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=normalized,
            )
        )


def _reply_stock_diagnosis(reply_token: str, stock_id: str) -> bool:
    report = services.get_stock_report(stock_id)
    if report is None:
        reply_message(reply_token, [V3TextMessage(text='❌ 查無此股票資料')])
        return True

    try:
        flex_msg = services.create_stock_flex_message(stock_id, report)
        reply_message(reply_token, [flex_msg])
        return True
    except Exception as exc:
        print(f'⚠️ Flex Message 建構失敗，降級為文字: {exc}')
        reply_message(reply_token, [V3TextMessage(text=services.format_stock_diagnosis(report))])
        return True


@app.route('/callback', methods=['POST'])
def callback():
    """LINE webhook callback route."""
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    except Exception as exc:
        print(f'❌ callback 處理失敗: {exc}')
        traceback.print_exc()

        try:
            payload = json.loads(body) if body else {}
            events = payload.get('events', []) if isinstance(payload, dict) else []
            reply_token = None
            if events and isinstance(events[0], dict):
                reply_token = events[0].get('replyToken')

            if reply_token:
                reply_message(reply_token, [V3TextMessage(text='⚠️ 系統忙碌中，請稍後再試一次')])
        except Exception as fallback_error:
            print(f'⚠️ callback fallback 回覆失敗: {fallback_error}')
    return 'OK'


@handler.add(PostbackEvent)
def postback_handler(event):
    """Handle Rich Menu postback actions."""
    data = getattr(getattr(event, 'postback', None), 'data', '')
    payload = services._parse_postback_payload(data)
    action = payload.get('action', '')
    source_id = services._extract_line_source_id(event)
    messages = services._build_postback_reply_messages(action, payload=payload, source_id=source_id)
    reply_message(event.reply_token, messages)


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    """Line 訊息處理中心。"""
    raw_text = event.message.text if event.message and event.message.text else ''
    msg_text = services._normalize_line_text(raw_text)
    msg_key = services._compact_command_key(msg_text)
    source_id = services._extract_line_source_id(event)

    pending_state = services._line_interaction_state.get(source_id)
    if pending_state and pending_state.get('action') == 'stock_diagnosis':
        if msg_text in ['取消', 'cancel', '結束']:
            services._line_interaction_state.clear(source_id)
            reply_message(event.reply_token, [V3TextMessage(text='✅ 已取消個股診斷流程')])
            return
        if re.fullmatch(r'\d{4}', msg_text):
            services._line_interaction_state.clear(source_id)
            _reply_stock_diagnosis(event.reply_token, msg_text)
            return

    diagnose_match = re.match(r'^診斷\s*(\d{4})?\s*$', msg_text)
    if diagnose_match:
        stock_id = diagnose_match.group(1)
        if stock_id:
            _reply_stock_diagnosis(event.reply_token, stock_id)
            return
        services._line_interaction_state.set(source_id, {'action': 'stock_diagnosis'})
        reply_message(event.reply_token, [V3TextMessage(text='🔎 請輸入 4 碼股票代號，例如 2330。')])
        return

    ctx = line_commands.LineCommandContext(
        event=event,
        msg_text=msg_text,
        msg_key=msg_key,
        services=services,
        reply_message=reply_message,
        reply_stock_diagnosis=_reply_stock_diagnosis,
    )
    command = line_commands.dispatch(ctx)
    reply = command.handler(ctx) if command else line_commands.build_help_message(services)
    if reply is None:
        return
    reply_message(event.reply_token, [V3TextMessage(text=reply)])
