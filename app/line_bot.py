"""Canonical LINE Bot webhook entrypoint and message handlers."""

from __future__ import annotations

import json
import re
import sys
import traceback

import pandas as pd

from . import app, handler

app_pkg = sys.modules[__package__]


def reply_message(reply_token: str, messages) -> None:
    """統一 LINE reply path，供 callback / postback / message handler 共用。"""
    normalized = messages if isinstance(messages, list) else [messages]
    with app_pkg.ApiClient(app_pkg.configuration) as api_client:
        line_bot_api = app_pkg.MessagingApi(api_client)
        line_bot_api.reply_message(
            app_pkg.ReplyMessageRequest(
                reply_token=reply_token,
                messages=normalized,
            )
        )


@app.route('/callback', methods=['POST'])
def callback():
    """LINE webhook callback route."""
    signature = app_pkg.request.headers['X-Line-Signature']
    body = app_pkg.request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except app_pkg.InvalidSignatureError:
        app_pkg.abort(400)
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
                reply_message(reply_token, [app_pkg.V3TextMessage(text='⚠️ 系統忙碌中，請稍後再試一次')])
        except Exception as fallback_error:
            print(f'⚠️ callback fallback 回覆失敗: {fallback_error}')
    return 'OK'


@handler.add(app_pkg.PostbackEvent)
def postback_handler(event):
    """Handle Rich Menu postback actions."""
    action = app_pkg._extract_postback_action(getattr(getattr(event, 'postback', None), 'data', ''))
    messages = app_pkg._build_postback_reply_messages(action)
    reply_message(event.reply_token, messages)


@handler.add(app_pkg.MessageEvent, message=app_pkg.TextMessageContent)
def handle_message(event):
    """Line 訊息處理中心。"""
    raw_text = event.message.text if event.message and event.message.text else ''
    msg_text = app_pkg._normalize_line_text(raw_text)
    msg_key = app_pkg._compact_command_key(msg_text)

    if msg_key in app_pkg.MODE_CMD_MAP:
        mode_label, preset_key = app_pkg.MODE_CMD_MAP[msg_key]
        emoji = app_pkg.MODE_EMOJI.get(preset_key, '')
        if app_pkg.update_setting('mode', preset_key):
            updates = {**app_pkg.V34_MODE_PRESETS[preset_key], **app_pkg.V35_MODE_PRESETS[preset_key]}
            all_ok = app_pkg._apply_settings_batch(updates)
            if all_ok:
                reply = f"{emoji} {app_pkg.MODE_REPLY_TEMPLATE[preset_key]}\n💡 直接輸入「推薦」即可生效"
            else:
                reply = f'⚠️ 模式已切換至{mode_label}，但部分 V34/V35 參數更新失敗'
        else:
            reply = '❌ 切換失敗，請稍後再試'

    elif any(
        app_pkg._is_quick_mode_cmd(msg_text, ver, style)
        for ver in ('34', '35')
        for style in ('aggressive', 'balanced', 'loose', 'conservative')
    ):
        reply = None
        for ver in ('34', '35'):
            for style in ('aggressive', 'balanced', 'loose', 'conservative'):
                if app_pkg._is_quick_mode_cmd(msg_text, ver, style):
                    presets = app_pkg.V34_MODE_PRESETS if ver == '34' else app_pkg.V35_MODE_PRESETS
                    all_ok = app_pkg._apply_settings_batch(presets[style])
                    emoji = app_pkg.MODE_EMOJI.get(style, '')
                    style_label = {
                        'aggressive': '積極',
                        'balanced': '平衡',
                        'loose': '寬鬆',
                        'conservative': '保守',
                    }[style]
                    if all_ok:
                        reply = f'{emoji} V{ver} 已設為{style_label}檔位'
                    else:
                        reply = f'❌ V{ver} {style_label}設定失敗'
                    break
            if reply:
                break

    elif re.match(r'^設定\s*[Vv]34\s*放寬\s+[-\d.]+\s+[-\d.]+\s+[-\d.]+\s*$', msg_text):
        try:
            value_str = re.sub(r'^設定\s*[Vv]34\s*放寬\s+', '', msg_text).strip()
            parts = value_str.split()
            if len(parts) != 3:
                reply = '❌ 格式錯誤\n用法：設定V34放寬 10 0.90 0.7'
            else:
                yoy_min = float(parts[0])
                breakout_ratio = float(parts[1])
                vol_min = float(parts[2])

                if not (-50 <= yoy_min <= 300 and 0.70 <= breakout_ratio <= 1.10 and 0.0 <= vol_min <= 5.0):
                    reply = '❌ 參數超出範圍\nYoY: -50~300, 突破: 0.70~1.10, 量比: 0~5'
                else:
                    updates = {
                        'v34_relaxed_revenue_yoy_min': str(yoy_min),
                        'v34_relaxed_breakout_ratio': str(breakout_ratio),
                        'v34_relaxed_volume_ratio_min': str(vol_min),
                    }
                    ok = all(app_pkg.update_setting(k, v) for k, v in updates.items())
                    reply = (
                        f'✅ V34 放寬參數已更新\nYoY>{yoy_min:.1f}% / 突破>={breakout_ratio:.2f} / 量比>{vol_min:.2f}'
                        if ok
                        else '❌ V34 放寬參數更新失敗'
                    )
        except ValueError:
            reply = '❌ 格式錯誤\n用法：設定V34放寬 10 0.90 0.7'

    elif re.match(r'^設定\s*[Vv]34\s+[-\d.]+\s+[-\d.]+\s+[-\d.]+\s*$', msg_text):
        try:
            value_str = re.sub(r'^設定\s*[Vv]34\s+', '', msg_text).strip()
            parts = value_str.split()
            if len(parts) != 3:
                reply = '❌ 格式錯誤\n用法：設定V34 18 0.93 0.9'
            else:
                yoy_min = float(parts[0])
                breakout_ratio = float(parts[1])
                vol_min = float(parts[2])

                if not (-50 <= yoy_min <= 300 and 0.70 <= breakout_ratio <= 1.10 and 0.0 <= vol_min <= 5.0):
                    reply = '❌ 參數超出範圍\nYoY: -50~300, 突破: 0.70~1.10, 量比: 0~5'
                else:
                    updates = {
                        'v34_revenue_yoy_min': str(yoy_min),
                        'v34_breakout_ratio': str(breakout_ratio),
                        'v34_volume_ratio_min': str(vol_min),
                    }
                    ok = all(app_pkg.update_setting(k, v) for k, v in updates.items())
                    reply = (
                        f'✅ V34 嚴格參數已更新\nYoY>{yoy_min:.1f}% / 突破>={breakout_ratio:.2f} / 量比>{vol_min:.2f}'
                        if ok
                        else '❌ V34 嚴格參數更新失敗'
                    )
        except ValueError:
            reply = '❌ 格式錯誤\n用法：設定V34 18 0.93 0.9'

    elif re.match(r'^設定\s*[Vv]35\s*放寬\s+[-\d.]+\s+[-\d.]+\s+[-\d.]+\s*$', msg_text):
        try:
            value_str = re.sub(r'^設定\s*[Vv]35\s*放寬\s+', '', msg_text).strip()
            parts = value_str.split()
            if len(parts) != 3:
                reply = '❌ 格式錯誤\n用法：設定V35放寬 4 -5 0.6'
            else:
                op_margin_pct = float(parts[0])
                revenue_yoy = float(parts[1])
                vol_min = float(parts[2])
                op_margin = op_margin_pct / 100
                if not (-20 <= op_margin_pct <= 100 and -100 <= revenue_yoy <= 300 and 0.0 <= vol_min <= 5.0):
                    reply = '❌ 參數超出範圍\n利益率%: -20~100, YoY: -100~300, 量比: 0~5'
                else:
                    updates = {
                        'v35_relaxed_op_margin_min': str(op_margin),
                        'v35_relaxed_revenue_yoy_min': str(revenue_yoy),
                        'v35_relaxed_volume_ratio_min': str(vol_min),
                    }
                    ok = all(app_pkg.update_setting(k, v) for k, v in updates.items())
                    reply = (
                        f'✅ V35 放寬參數已更新\n利益率>{op_margin_pct:.1f}% / YoY>{revenue_yoy:.1f}% / 量比>{vol_min:.2f}'
                        if ok
                        else '❌ V35 放寬參數更新失敗'
                    )
        except ValueError:
            reply = '❌ 格式錯誤\n用法：設定V35放寬 4 -5 0.6'

    elif re.match(r'^設定\s*[Vv]35\s+[-\d.]+\s+[-\d.]+\s+[-\d.]+\s*$', msg_text):
        try:
            value_str = re.sub(r'^設定\s*[Vv]35\s+', '', msg_text).strip()
            parts = value_str.split()
            if len(parts) != 3:
                reply = '❌ 格式錯誤\n用法：設定V35 6 0 0.8'
            else:
                op_margin_pct = float(parts[0])
                revenue_yoy = float(parts[1])
                vol_min = float(parts[2])
                op_margin = op_margin_pct / 100
                if not (-20 <= op_margin_pct <= 100 and -100 <= revenue_yoy <= 300 and 0.0 <= vol_min <= 5.0):
                    reply = '❌ 參數超出範圍\n利益率%: -20~100, YoY: -100~300, 量比: 0~5'
                else:
                    updates = {
                        'v35_op_margin_min': str(op_margin),
                        'v35_revenue_yoy_min': str(revenue_yoy),
                        'v35_volume_ratio_min': str(vol_min),
                    }
                    ok = all(app_pkg.update_setting(k, v) for k, v in updates.items())
                    reply = (
                        f'✅ V35 嚴格參數已更新\n利益率>{op_margin_pct:.1f}% / YoY>{revenue_yoy:.1f}% / 量比>{vol_min:.2f}'
                        if ok
                        else '❌ V35 嚴格參數更新失敗'
                    )
        except ValueError:
            reply = '❌ 格式錯誤\n用法：設定V35 6 0 0.8'

    elif msg_text.startswith('設定信心'):
        try:
            value_str = msg_text.replace('設定信心', '').strip()
            value = float(value_str) / 100
            is_valid, err_msg = app_pkg.validate_setting('ai_threshold', str(value))
            if not is_valid:
                reply = f'❌ {err_msg}\n範例：設定信心 60（代表60%）'
            elif app_pkg.update_setting('ai_threshold', str(value)):
                reply = f'🧠 AI 信心門檻已設為 {int(value*100)}%\n將只推薦高於此門檻的股票'
            else:
                reply = '❌ 設定失敗'
        except ValueError:
            reply = '❌ 格式錯誤\n正確用法：設定信心 60'

    elif msg_text.startswith('設定停損'):
        try:
            value_str = msg_text.replace('設定停損', '').strip()
            value = float(value_str) / 100
            if 0.01 <= value <= 0.20:
                if app_pkg.update_setting('v30_stop_loss', str(value)):
                    reply = f'🛡️ V30停損已設為 {int(value*100)}%\n下次選股將使用新參數'
                else:
                    reply = '❌ 設定失敗'
            else:
                reply = '❌ 停損需在 1%-20% 之間\n範例：設定停損 5'
        except ValueError:
            reply = '❌ 格式錯誤\n正確用法：設定停損 5（代表5%）'

    elif msg_text.startswith('設定停利'):
        try:
            value_str = msg_text.replace('設定停利', '').strip()
            if value_str == '0' or value_str.lower() == '不停利':
                if app_pkg.update_setting('v30_take_profit', '0.0'):
                    params = app_pkg.get_v30_params_from_db()
                    reply = f'🎯 V30停利已取消\n將持有至停損或到期（{params["MAX_HOLD_DAYS"]}天）'
                else:
                    reply = '❌ 設定失敗'
            else:
                value = float(value_str) / 100
                if 0.05 <= value <= 0.50:
                    if app_pkg.update_setting('v30_take_profit', str(value)):
                        reply = f'🎯 V30停利已設為 {int(value*100)}%\n下次選股將使用新參數'
                    else:
                        reply = '❌ 設定失敗'
                else:
                    reply = '❌ 停利需在 5%-50% 之間\n範例：設定停利 20（代表20%）\n或輸入「設定停利 0」取消停利'
        except ValueError:
            reply = '❌ 格式錯誤\n用法：\n• 設定停利 20（20%停利）\n• 設定停利 0（不停利）'

    elif msg_text == '查看設定':
        reply = app_pkg.get_settings_info()

    elif app_pkg._match_strategy_switch(msg_text.lower()):
        strategy_key, strategy_display, features_text = app_pkg._match_strategy_switch(msg_text.lower())
        try:
            mgr = app_pkg.StrategyManager()
            if mgr.set_active_strategy(strategy_key):
                reply = f'🔄 已切換至【{strategy_display}】\n\n'
                reply += '🎯 特色：\n'
                reply += features_text
                reply += '\n💡 輸入「推薦」開始選股'
            else:
                reply = '❌ 切換失敗，請稍後再試'
        except Exception as exc:
            reply = f'❌ 切換失敗: {exc}'

    elif msg_text in ['查看策略', '目前策略', '策略狀態']:
        try:
            mgr = app_pkg.StrategyManager()
            active = mgr.get_active_strategy()
            reply = '📊 【目前使用策略】\n\n'
            reply += f'🎯 策略：{active.display_name}\n'
            reply += f'📝 說明：{active.description}\n'
            reply += f'🛡️ 停損：{active.stop_loss*100:.0f}%\n'
            reply += f'🎯 停利：{active.take_profit*100:.0f}%\n'
            reply += f'⏰ 最長持有：{active.max_hold_days} 天\n\n'
            reply += '💡 可用切換指令：\n'
            reply += '• 切換V30 → V31 混合策略（均衡）\n'
            reply += '• 切換V33 → 低波動策略（穩健）\n'
            reply += '• 切換V34 → 雙渦輪策略（積極）\n'
            reply += '• 切換V35 → 經營效益策略（成長）\n'
            reply += '• 切換V36 → 籌碼動能策略（追蹤）\n'
            reply += '• 切換V37 → 均值回歸策略（反轉）\n'
            reply += '• 切換V38 → 高殖利率策略（價值）\n'
        except Exception as exc:
            reply = f'❌ 查詢策略失敗: {exc}'

    elif msg_text in ['V30', 'v30', '策略']:
        reply = app_pkg.get_v30_recommendation()

    elif msg_text in ['推薦', '選股', 'AI']:
        try:
            flex_msg = app_pkg.get_strategy_recommendation(as_flex=True)
            if isinstance(flex_msg, str):
                reply = flex_msg
            else:
                reply_message(event.reply_token, [flex_msg])
                return
        except Exception as exc:
            print(f'⚠️ Flex Carousel 建構失敗，降級為純文字: {exc}')
            reply = app_pkg.get_strategy_recommendation(as_flex=False)

    elif msg_text.isdigit() and len(msg_text) == 4:
        report = app_pkg.get_stock_report(msg_text)
        if report is not None:
            try:
                flex_msg = app_pkg.create_stock_flex_message(msg_text, report)
                reply_message(event.reply_token, [flex_msg])
                return
            except Exception as exc:
                print(f'⚠️ Flex Message 建構失敗，降級為文字: {exc}')
                reply = app_pkg.format_stock_diagnosis(report)
        else:
            reply = '❌ 查無此股票資料'

    elif msg_text.startswith('查詢'):
        stock_id = msg_text.replace('查詢', '').strip()
        if stock_id.isdigit():
            reply = app_pkg.query_stock(stock_id)
        else:
            reply = '❌ 請輸入正確的股票代號'

    elif msg_text in ['持股', '目前持股', 'AI持股', '庫存']:
        try:
            rows = app_pkg.get_open_holdings(limit=10)
            if rows:
                holdings_list = []
                for row in rows:
                    entry_p = float(row[2]) if row[2] else 0
                    current_p = float(row[4]) if row[4] else entry_p
                    pnl = (current_p - entry_p) / entry_p if entry_p > 0 else 0
                    trade_dt = row[3]
                    days = (pd.Timestamp.now() - pd.Timestamp(trade_dt)).days if trade_dt else 0
                    holdings_list.append(
                        {
                            'stock_id': str(row[0]),
                            'entry_price': entry_p,
                            'current_price': current_p,
                            'pnl_pct': pnl,
                            'hold_days': days,
                            'strategy': str(row[1]) if row[1] else '',
                        }
                    )
                flex_msg = app_pkg.create_holdings_flex(
                    holdings=holdings_list,
                    strategy_name=holdings_list[0].get('strategy', ''),
                    date_str=app_pkg._resolve_ui_baseline_date() or str(pd.Timestamp.now().date()),
                )
                reply_message(event.reply_token, [flex_msg])
                return
            reply = '📭 目前無 AI 持股\n輸入「推薦」取得今日選股建議'
        except Exception as exc:
            print(f'⚠️ 持股查詢失敗: {exc}')
            reply = '📭 目前無持股紀錄\n輸入「推薦」取得今日選股建議'

    elif msg_text in ['日誌', '反思', 'journal']:
        reply = app_pkg._build_journal_reflection_text()

    elif msg_text in ['回測', '績效', '回測結果', 'backtest']:
        try:
            from core.viz_helper import get_backtest_summary

            summary = get_backtest_summary()
            if summary:
                metrics = {
                    'total_return': summary.get('total_roi', 0),
                    'max_drawdown': summary.get('max_drawdown', 0),
                    'sharpe_ratio': summary.get('sharpe_ratio', 0),
                    'win_rate': summary.get('win_rate', 0),
                    'total_trades': summary.get('trade_count', 0),
                    'period': summary.get('period', '最近回測'),
                }
                mgr = app_pkg.StrategyManager()
                active = mgr.get_active_strategy()
                sname = active.display_name if active else ''
                flex_msg = app_pkg.create_backtest_summary_flex(metrics, strategy_name=sname)
                reply_message(event.reply_token, [flex_msg])
                return
            reply = '📊 尚無回測數據\n請先執行 python jobs/run_backtest.py'
        except Exception as exc:
            print(f'⚠️ 回測摘要失敗: {exc}')
            reply = '📊 尚無回測數據\n請先執行 python jobs/run_backtest.py'

    elif msg_text in ['dashboard', '儀表板', 'Dashboard', '看板']:
        dashboard_base = app_pkg.get_ngrok_url()
        dashboard_url = f'{dashboard_base}/dashboard'
        is_ngrok = 'ngrok' in dashboard_base
        url_hint = '（ngrok 公開連結，可直接點擊）' if is_ngrok else '（本機連結，需在同一網路）'
        reply = '📊 【V32 量化交易儀表板】\n\n'
        reply += f'🔗 Dashboard URL {url_hint}:\n'
        reply += f'{dashboard_url}\n\n'
        reply += '📈 功能:\n'
        reply += '• 資產曲線圖 (Equity Curve)\n'
        reply += '• 回測績效指標 (ROI, MDD, Sharpe)\n'
        reply += '• 交易明細表 (Recent Trades)\n'
        reply += '• 即時選股訊號 (Live Signals)\n\n'
        reply += '💡 提示: 請在電腦瀏覽器開啟以獲得最佳體驗'

    elif msg_text in ['新聞', 'News', 'news', '早報']:
        try:
            import datetime as _dt
            from core.news_agent import get_morning_news_summary

            news_summary = get_morning_news_summary()
            today_str = _dt.datetime.now().strftime('%Y-%m-%d')
            news_flex = app_pkg.create_news_flex(news_summary, today_str)
            picks_flex = app_pkg.get_strategy_recommendation(as_flex=True)

            messages = [news_flex]
            if not isinstance(picks_flex, str):
                messages.append(picks_flex)

            reply_message(event.reply_token, messages)
            return
        except Exception as exc:
            print(f'⚠️ 新聞 Flex 失敗，降級為純文字: {exc}')
            traceback.print_exc()
            try:
                from core.news_agent import get_morning_news_summary

                news_summary = get_morning_news_summary()
                picks_text = app_pkg.get_strategy_recommendation(as_flex=False)
                divider = '=' * 28
                reply = f'📰 【今日新聞摘要】\n\n{news_summary}\n\n{divider}\n\n{picks_text}'
                if len(reply) > 4900:
                    reply = reply[:4900] + '\n...'
            except Exception as inner_exc:
                reply = f'❌ 新聞取得失敗: {str(inner_exc)[:100]}'

    else:
        try:
            mgr = app_pkg.StrategyManager()
            current = mgr.get_active_strategy()
            current_name = current.display_name if current else 'V31 混合策略'
        except Exception:
            current_name = '未知'

        reply = '🤖 【StockAI Line Bot V3.0】\n'
        reply += f'📌 目前策略：{current_name}\n'
        reply += '\n📋 指令清單:\n'
        reply += '-' * 30 + '\n'
        reply += '【🎯 選股功能】\n'
        reply += '• 推薦 → 使用當前策略選出前5名\n'
        reply += '• 新聞 → 今日新聞摘要 + 選股推薦\n'
        reply += '• V30 → 純技術分析選股\n'
        reply += '• 2330 → 個股 AI 健康診斷\n'
        reply += '• 持股 → 查看 AI 模擬持股\n'
        reply += '• 回測 → 最新回測績效摘要\n'
        reply += '\n【🔄 策略切換】\n'
        reply += '• 切換V30 → V31 混合（均衡型）\n'
        reply += '• 切換V33 → 低波動（穩健型）\n'
        reply += '• 切換V34 → 雙渦輪（積極型）\n'
        reply += '• 切換V35 → 經營效益（成長型）\n'
        reply += '• 切換V36 → 籌碼動能（追蹤型）\n'
        reply += '• 切換V37 → 均值回歸（反轉型）\n'
        reply += '• 切換V38 → 高殖利率（價值型）\n'
        reply += '• 查看策略 → 顯示目前策略詳情\n'
        reply += '\n【📊 Dashboard】\n'
        reply += '• dashboard → 開啟視覺化儀表板\n'
        reply += '\n【⚙️ V30 參數調整】\n'
        reply += '• 設定停損 5 (停損5%)\n'
        reply += '• 設定停利 20 (停利20%)\n'
        reply += '• 設定停利 0 (不停利)\n'
        reply += '• 查看設定\n'
        reply += '\n【🚀 V34/V35 參數調整】\n'
        reply += '• 切換積極 / 切換平衡 / 切換寬鬆\n'
        reply += '• V34積極 / V34平衡 / V34寬鬆\n'
        reply += '• V35積極 / V35平衡 / V35寬鬆\n'
        reply += '• 設定V34 18 0.93 0.9\n'
        reply += '• 設定V34放寬 10 0.90 0.7\n'
        reply += '• 設定V35 6 0 0.8\n'
        reply += '• 設定V35放寬 4 -5 0.6\n'
        reply += '-' * 30 + '\n'
        reply += '💡 輸入「推薦」開始選股\n'
        reply += '⚠️ 所有推薦僅供參考'

    reply_message(event.reply_token, [app_pkg.V3TextMessage(text=reply)])