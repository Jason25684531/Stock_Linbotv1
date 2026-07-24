"""Canonical application package integrating Web and LINE entrypoints."""

# -*- coding: utf-8 -*-
from contextlib import contextmanager
import json
import os
from pathlib import Path
import random
import re
import sys
import threading
import time
import traceback
import unicodedata
from datetime import datetime
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import pandas as pd
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage as V3TextMessage,
)
from linebot.v3.webhooks import MessageEvent, PostbackEvent, TextMessageContent

from config import Config
from config import (
    MODE_CMD_MAP,
    MODE_EMOJI,
    MODE_REPLY_TEMPLATE,
    V34_MODE_PRESETS,
    V35_MODE_PRESETS,
)
from core.db_helper import (
    build_dashboard_aggregation_cache_key,
    create_user_simulation_trade,
    format_market_fallback_notice,
    get_actual_latest_date,
    get_backtest_trades,
    get_backtest_equity_curve,
    get_daily_recommendations,
    get_backtest_summary_from_db,
    get_latest_trade_date,
    get_news_sentiment,
    get_open_holdings,
    get_recent_backtest_trades,
    get_recommendations_with_market_fallback,
    get_setting,
    get_stock_data,
    get_stock_history,
    get_stock_sector,
    merge_recommendations_with_market_data,
    normalize_date_str,
    resolve_dashboard_aggregation_cache,
    safe_float,
    safe_int,
    supplement_financial_data,
    update_setting,
    validate_setting,
)
from core.calc_indicators import calculate_kd_full, calculate_rsi
from core.line_message_builder import (
    build_backtest_reflection_flex,
    build_macro_summary_flex,
    build_strategy_prompt_flex,
    create_backtest_summary_flex,
    create_empty_state_flex,
    create_holdings_flex,
    create_journal_reflection_flex,
    create_news_flex,
    create_recommendation_carousel,
    create_strategy_picker_message,
    create_stock_flex_message,
)
from core.mcp_client import MCPClientError, TWSEMCPClient as MCPClient
from core.report_helper import format_stock_diagnosis, get_stock_report
from core.strategy import (
    calculate_v30_signal,
    format_stock_query,
    format_v30_recommendation,
    format_v31_recommendation,
    get_best_stocks_v31_hybrid,
    get_v30_params_from_db,
)
from core.strategy_manager import StrategyManager


if sys.platform == 'win32':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')


REPO_ROOT = Path(__file__).resolve().parents[1]
app = Flask(
    __name__,
    template_folder=str(REPO_ROOT / 'templates'),
)
app.secret_key = Config.FLASK_SECRET_KEY


login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = '請先登入以存取此頁面'
login_manager.login_message_category = 'error'


class User(UserMixin):
    """簡易使用者類別 (基於環境變數驗證)"""

    def __init__(self, user_id):
        self.id = user_id

    @staticmethod
    def validate_password(password):
        return password == Config.ADMIN_PASSWORD

    @staticmethod
    def get(user_id):
        if user_id == 'admin':
            return User('admin')
        return None


@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)


configuration = Configuration(access_token=Config.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(Config.LINE_CHANNEL_SECRET)

print('[AI] 模型將按策略動態載入')
print('[AI] 正在初始化策略管理器...')
strategy_manager = StrategyManager()
print(f"[OK] 當前策略: {strategy_manager.get_active_strategy_name()}")


from .news_overlay import (  # noqa: E402
    _apply_news_sentiment_overlay,
    _get_current_stock_news_deadline,
    _get_sector_news_summary,
    _get_stock_mentions_map,
    _get_stock_specific_news_summary,
    _live_signal_news_timeout_scope,
    _parse_news_reason,
    _resolve_signal_news_info,
    _stock_news_runtime,
)
from .dashboard_payloads import (  # noqa: E402
    _PostbackCache,
    _postback_cache,
    _WAR_ROOM_DEFAULT_OVERLAYS,
    _WAR_ROOM_DEFAULT_PANES,
    _WAR_ROOM_OVERLAY_OPTIONS,
    _WAR_ROOM_PANE_OPTIONS,
    _WAR_ROOM_PERIOD_LABELS,
    _aggregate_dashboard_history,
    _build_chip_snapshot,
    _build_dashboard_action_scripts,
    _build_dashboard_health_check_payload,
    _build_dashboard_health_check_payload_local,
    _build_dashboard_llm_report,
    _build_dashboard_macro_payload,
    _build_dashboard_macro_payload_local,
    _build_dashboard_rule_report,
    _build_dashboard_signal_light,
    _build_empty_war_room_payload,
    _build_market_snapshot,
    _build_war_room_chip_flow_summary,
    _build_war_room_flow_pane,
    _build_war_room_fundamentals,
    _build_war_room_price_map,
    _build_war_room_quant_status,
    _build_war_room_selected_panes,
    _build_war_room_structure,
    _build_war_room_tactical_summary,
    _build_war_room_view_state,
    _calculate_macd_components,
    _count_dashboard_series_points,
    _format_price,
    _format_price_range,
    _format_war_room_levels,
    _merge_dashboard_series_payload,
    _merge_unique_strings,
    _normalize_war_room_period,
    _normalize_war_room_selection,
    _overlay_dashboard_health_check_payload,
    _overlay_dashboard_macro_payload,
    _prepare_dashboard_history_frame,
    _safe_price,
    _sanitize_dashboard_json,
    _select_dashboard_status,
    _serialize_dashboard_value_series,
    _summarize_chip_snapshot,
    _summarize_market_snapshot,
)
from .line_flows import (  # noqa: E402
    _alias,
    _info,
    _key,
    _STRATEGY_ALIAS_INDEX,
    _STRATEGY_SWITCH_MAP,
    _LineInteractionStateStore,
    _build_backtest_reflection_messages,
    _build_chip_trend_messages,
    _build_journal_reflection_messages,
    _build_journal_reflection_snapshot,
    _build_journal_reflection_text,
    _build_macro_news_messages,
    _build_market_summary_messages,
    _build_postback_empty_state,
    _build_random_strategy_messages,
    _build_selected_strategy_messages,
    _build_stock_diagnosis_prompt_messages,
    _build_strategy_backtest_snapshot,
    _build_strategy_picker_messages,
    _build_strategy_reflection_suggestions,
    _calculate_trade_sequence_drawdown,
    _compact_command_key,
    _extract_line_source_id,
    _format_backtest_trade_summary,
    _get_strategy_display_name,
    _get_strategy_payload_key,
    _is_quick_mode_cmd,
    _line_interaction_state,
    _list_strategy_picker_options,
    _load_backtest_summary_snapshot,
    _load_strategy_backtest_frame,
    _match_strategy_switch,
    _normalize_line_text,
    _normalize_strategy_request_key,
    _parse_postback_payload,
    _summarize_today_pick_status,
)


def get_ngrok_url() -> str:
    """自動偵測 ngrok 公開 URL。"""
    import json as _json
    import urllib.request

    ngrok_api = 'http://localhost:4040/api/tunnels'
    try:
        with urllib.request.urlopen(ngrok_api, timeout=2) as resp:
            data = _json.loads(resp.read().decode('utf-8'))
        tunnels = data.get('tunnels', [])
        for tunnel in tunnels:
            if tunnel.get('proto') == 'https':
                return tunnel['public_url'].rstrip('/')
        if tunnels:
            return tunnels[0]['public_url'].rstrip('/')
    except Exception:
        pass
    return Config.PUBLIC_DASHBOARD_URL.rstrip('/')


def _normalize_backtest_dates(start_date, end_date):
    from datetime import datetime as _datetime
    from datetime import timedelta

    if not start_date:
        start_date = (_datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    if not end_date:
        end_date = _datetime.now().strftime('%Y-%m-%d')
    return start_date, end_date


def _get_backtest_module():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(base_dir)
    if root_dir not in sys.path:
        sys.path.insert(0, root_dir)
    from importlib import import_module
    return import_module('4_run_backtest')


def _run_portfolio_backtest(selected_strategies, start_date=None, end_date=None, weights=None):
    start_date, end_date = _normalize_backtest_dates(start_date, end_date)
    backtest_module = _get_backtest_module()
    engine = backtest_module.PortfolioBacktestEngine(
        strategies=selected_strategies,
        start_date=start_date,
        end_date=end_date,
        weights=weights,
    )
    result = engine.run_portfolio_backtest()
    return result, start_date, end_date


def _load_backtest_summary_or_error(error_message):
    summary = get_backtest_summary_from_db()
    if summary is None:
        from core.viz_helper import get_backtest_summary

        summary = get_backtest_summary()
    if summary is None:
        return None, (jsonify({'error': error_message}), 404)
    return summary, None


def _apply_settings_batch(updates):
    return all(update_setting(key, value) for key, value in updates.items())


def _build_summary_response(summary):
    return {
        'total_roi': summary['total_roi'],
        'win_rate': summary['win_rate'],
        'mdd': summary['max_drawdown'],
        'sharpe': summary['sharpe_ratio'],
        'trade_count': summary['trade_count'],
        'avg_hold_days': summary['avg_hold_days'],
    }


def _resolve_ui_baseline_date() -> str | None:
    """取得 UI 與 LINE 共用的資料基準日。"""
    return normalize_date_str(get_actual_latest_date() or get_latest_trade_date())


def _current_line_date() -> str:
    return _resolve_ui_baseline_date() or datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d')


def _extract_postback_action(data: str) -> str:
    return _parse_postback_payload(data).get('action', '')


def _register_postback_handlers() -> dict:
    return {
        'prompt_stock_diagnosis': lambda payload=None, source_id='': _build_stock_diagnosis_prompt_messages(source_id),
        'macro_summary': lambda payload=None, source_id='': _build_macro_news_messages(),
        'journal_reflection': lambda payload=None, source_id='': _build_journal_reflection_messages(),
        'choose_strategy': lambda payload=None, source_id='': _build_strategy_picker_messages(),
        'backtest_reflect': lambda payload=None, source_id='': _build_backtest_reflection_messages(payload),
        'strategy_select': lambda payload=None, source_id='': _build_selected_strategy_messages(payload),
        'select_strategy': lambda payload=None, source_id='': _build_selected_strategy_messages(payload),
        'get_macro_news': lambda payload=None, source_id='': _build_macro_news_messages(),
        'get_journal': lambda payload=None, source_id='': _build_journal_reflection_messages(),
        'market_summary': lambda payload=None, source_id='': _build_market_summary_messages(),
        'chip_trend': lambda payload=None, source_id='': _build_chip_trend_messages(),
        'random_strategy': lambda payload=None, source_id='': _build_random_strategy_messages(),
    }


def _build_postback_reply_messages(action: str, payload: dict[str, str] | None = None, source_id: str = '') -> list:
    handler_fn = _register_postback_handlers().get(action)
    if handler_fn is not None:
        return handler_fn(payload=payload, source_id=source_id)
    return [
        _build_postback_empty_state(
            title='⚠️ Rich Menu',
            message='尚未支援的 Rich Menu 指令。',
        )
    ]


def _load_strategy_candidates(active, strategy_key: str, market_df: pd.DataFrame, requested_date: str, limit: int) -> tuple[pd.DataFrame, dict, bool]:
    persisted, meta = get_recommendations_with_market_fallback(
        date_str=requested_date,
        strategy=strategy_key,
        limit=limit,
        max_fallback_age_days=Config.RECOMMENDATION_FALLBACK_MAX_AGE_DAYS,
    )
    if not persisted.empty:
        return merge_recommendations_with_market_data(persisted, market_df), meta, True
    return pd.DataFrame(), meta, bool(meta.get('has_persisted_snapshot', False))


def get_v30_recommendation():
    try:
        baseline_date = _resolve_ui_baseline_date()
        df, date_str = get_stock_data(date_str=baseline_date) if baseline_date else get_stock_data()
        if df.empty:
            return '💤 今日無資料'

        required_cols = ['close_price', 'ma20', 'ma60', 'volume', 'rsi']
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            return f"⚠️ 資料庫缺少欄位: {', '.join(missing_cols)}\n請執行 core/calc_indicators.py"

        picks = []
        for _, row in df.iterrows():
            v30_result = calculate_v30_signal(row)
            if v30_result['signal_strength'] == 'strong':
                picks.append(
                    {
                        'stock_id': row['stock_id'],
                        'close_price': row['close_price'],
                        'rsi': row.get('rsi', 0),
                        'volume': row.get('volume', 0),
                        'stop_loss': v30_result['stop_loss'],
                        'take_profit': v30_result['take_profit'],
                        'foreign_buy': row.get('foreign_buy', 0),
                    }
                )
        return format_v30_recommendation(picks, date_str)
    except Exception as exc:
        print(f'❌ V30 推薦失敗: {exc}')
        traceback.print_exc()
        return f'❌ 運算錯誤: {str(exc)[:100]}'


def get_strategy_recommendation(as_flex: bool = False, strategy_key: str | None = None):
    try:
        mgr = StrategyManager()
        active = mgr.get_strategy(strategy_key) if strategy_key else mgr.get_active_strategy()
        if active is None:
            return '❌ 策略載入失敗，請先輸入「切換V30」設定策略'

        strategy_name = active.display_name
        active_strategy_key = active.name

        requested_date = _current_line_date()
        baseline_date = _resolve_ui_baseline_date()
        df, date_str = get_stock_data(date_str=baseline_date) if baseline_date else get_stock_data()
        if df.empty:
            return '💤 今日無資料\n請確認已執行 python jobs/update_database.py'

        df = supplement_financial_data(df)
        candidates, fallback_meta, has_persisted = _load_strategy_candidates(
            active=active,
            strategy_key=active_strategy_key,
            market_df=df,
            requested_date=requested_date,
            limit=5,
        )
        display_date = fallback_meta.get('recommendation_date') or date_str
        market_notice = format_market_fallback_notice(fallback_meta, strategy_name)

        if candidates.empty:
            warning_block = f'{market_notice}\n\n' if market_notice else ''
            if as_flex:
                subtitle = f'📅 {display_date}'
                if market_notice:
                    subtitle += f'｜{market_notice}'
                return create_empty_state_flex(
                    title=f'🎯 {strategy_name}',
                    message='今日無符合條件的股票，可改看其他策略或等待下一個交易日。',
                    date_str=display_date,
                    subtitle=subtitle,
                )
            return (
                f'🔍 【{strategy_name}】選股結果\n'
                f'日期：{display_date}\n\n'
                f'{warning_block}'
                '❌ 今日無符合條件的股票\n\n'
                '💡 建議：\n'
                '• 觀望等待進場訊號\n'
                '• 嘗試「切換V33」低波動等其他策略\n'
                '• 輸入「查看策略」檢視篩選條件'
            )

        has_ai = bool(has_persisted and 'ai_score' in candidates.columns and candidates['ai_score'].notna().any())

        top_n = candidates.head(5)
        total_count = len(candidates)
        stock_mentions_map = _get_stock_mentions_map([str(sid) for sid in top_n['stock_id'].tolist()])

        if as_flex:
            picks_list = []
            for _, row in top_n.iterrows():
                close = row.get('close_price', 0)
                sid = str(row.get('stock_id', '????'))
                news_info = _resolve_signal_news_info(row, display_date, stock_mentions_map)
                picks_list.append(
                    {
                        'stock_id': sid,
                        'sector': get_stock_sector(sid),
                        'close_price': close,
                        'ai_score': row.get('ai_score', 0) if has_ai else 0,
                        'rsi': row.get('rsi', 0),
                        'volume': row.get('volume', 0),
                        'news_boost_reason': news_info['raw'],
                        'news_reason_items': news_info['items'],
                        'news_signal_title': news_info['title'],
                        'news_is_bearish': news_info['is_bearish'],
                        'stop_loss_price': close * (1 - active.stop_loss),
                        'take_profit_price': close * (1 + active.take_profit) if active.take_profit > 0 else 0,
                    }
                )
            return create_recommendation_carousel(
                picks=picks_list,
                strategy_name=strategy_name,
                date_str=display_date,
            )

        reply = f'🎯 【{strategy_name}】推薦\n'
        reply += f'📅 日期：{display_date}\n'
        if has_ai:
            reply += '🤖 AI 排名：已啟用\n'
        if market_notice:
            reply += f'{market_notice}\n'
        reply += '-' * 28 + '\n\n'

        for i, (_, row) in enumerate(top_n.iterrows(), 1):
            stock_id = row.get('stock_id', 'N/A')
            close = row.get('close_price', 0)
            rsi = row.get('rsi', 0)
            volume = row.get('volume', 0)
            sector = get_stock_sector(str(stock_id))
            news_info = _resolve_signal_news_info(row, display_date, stock_mentions_map)
            news_reason = news_info['raw']

            sl_price = close * (1 - active.stop_loss)
            tp_price = close * (1 + active.take_profit) if active.take_profit > 0 else 0

            reply += f"{'🥇🥈🥉'[i-1] if i <= 3 else '▪️'} {i}. {stock_id}（{sector}）\n"
            reply += f'   💰 收盤：{close:.2f}'
            if has_ai:
                ai_pct = row.get('ai_score', 0) * 100
                reply += f'  🤖 {ai_pct:.0f}分'
            reply += '\n'
            reply += f'   📊 RSI: {rsi:.1f}'
            if volume > 0:
                reply += f'  📈 量: {volume/10000:.0f}萬'
            reply += '\n'
            if news_reason:
                reply += f"   {news_info['title']}\n"
                for item in news_info['items'][:3]:
                    reply += f'   • {item}\n'
            reply += f'   🛡️ 停損: {sl_price:.2f}'
            if tp_price > 0:
                reply += f'  🎯 停利: {tp_price:.2f}'
            reply += '\n\n'

        reply += '-' * 28 + '\n'
        reply += f'📊 共篩選出 {total_count} 檔'
        if total_count > 5:
            reply += '（顯示前 5 名）'
        reply += f'\n⏰ 最長持有：{active.max_hold_days} 天\n'
        reply += '⚠️ 僅供參考，請自行評估風險'
        return reply
    except Exception as exc:
        print(f'❌ 策略推薦失敗: {exc}')
        traceback.print_exc()
        return f'❌ 策略推薦失敗: {str(exc)[:100]}'


def query_stock(stock_id):
    try:
        baseline_date = _resolve_ui_baseline_date()
        query_kwargs = {'stock_id': stock_id}
        if baseline_date:
            query_kwargs['date_str'] = baseline_date
        df, date_str = get_stock_data(**query_kwargs)
        if df.empty:
            return f'🔍 找不到 {stock_id} 的資料'

        row = df.iloc[0]
        prob = 0.5
        try:
            from core.model_utils import load_model as _load_model

            mgr = StrategyManager()
            names = mgr.get_active_strategy_names()
            skey = names[0] if names else None
            model, features, _, _ = _load_model(skey)
            if model and features:
                df_feat = pd.DataFrame([row])
                for feature_name in features:
                    if feature_name not in df_feat.columns:
                        df_feat[feature_name] = 0
                prob = model.predict_proba(df_feat[features].fillna(0))[:, 1][0]
        except Exception:
            pass

        enable_strategy = get_setting('enable_strategy_report', 'true') == 'true'
        return format_stock_query(stock_id, date_str, row, prob, enable_strategy)
    except Exception as exc:
        print(f'❌ 個股查詢失敗: {exc}')
        traceback.print_exc()
        return f'❌ 查詢失敗: {str(exc)[:100]}'


def get_settings_info():
    try:
        ai_threshold = float(get_setting('ai_threshold', '0.5'))
        v30_stop_loss = float(get_setting('v30_stop_loss', str(Config.V30_PARAMS['STOP_LOSS'])))
        v30_take_profit = float(get_setting('v30_take_profit', str(Config.V30_PARAMS['TAKE_PROFIT'])))
        v30_max_hold = int(get_setting('v30_max_hold_days', str(Config.V30_PARAMS['MAX_HOLD_DAYS'])))

        v34_yoy = float(get_setting('v34_revenue_yoy_min', str(Config.V34_REVENUE_YOY_MIN)))
        v34_breakout = float(get_setting('v34_breakout_ratio', str(Config.V34_BREAKOUT_RATIO)))
        v34_volume = float(get_setting('v34_volume_ratio_min', str(Config.V34_VOLUME_RATIO_MIN)))
        v34_relaxed_yoy = float(get_setting('v34_relaxed_revenue_yoy_min', str(Config.V34_RELAXED_REVENUE_YOY_MIN)))
        v34_relaxed_breakout = float(get_setting('v34_relaxed_breakout_ratio', str(Config.V34_RELAXED_BREAKOUT_RATIO)))
        v34_relaxed_volume = float(get_setting('v34_relaxed_volume_ratio_min', str(Config.V34_RELAXED_VOLUME_RATIO_MIN)))

        v35_op = float(get_setting('v35_op_margin_min', str(Config.V35_OP_MARGIN_MIN)))
        v35_revenue = float(get_setting('v35_revenue_yoy_min', str(Config.V35_REVENUE_YOY_MIN)))
        v35_volume = float(get_setting('v35_volume_ratio_min', str(Config.V35_VOLUME_RATIO_MIN)))
        v35_relaxed_op = float(get_setting('v35_relaxed_op_margin_min', str(Config.V35_RELAXED_OP_MARGIN_MIN)))
        v35_relaxed_revenue = float(get_setting('v35_relaxed_revenue_yoy_min', str(Config.V35_RELAXED_REVENUE_YOY_MIN)))
        v35_relaxed_volume = float(get_setting('v35_relaxed_volume_ratio_min', str(Config.V35_RELAXED_VOLUME_RATIO_MIN)))

        msg = '⚙️ 【當前設定】\n'
        msg += '-' * 30 + '\n'
        msg += '🚀 V30 策略參數:\n'
        msg += f'  🛡️ 停損: {int(v30_stop_loss*100)}%\n'
        if v30_take_profit > 0:
            msg += f'  🎯 停利: {int(v30_take_profit*100)}%\n'
        else:
            msg += f'  🎯 停利: 不停利（持有至到期）\n'
        msg += f'  ⏰ 最長持有: {v30_max_hold}天\n\n'
        msg += '🚀 V34 參數（嚴格 / 放寬）:\n'
        msg += f'  📈 YoY: {v34_yoy:.1f}% / {v34_relaxed_yoy:.1f}%\n'
        msg += f'  💥 突破: {v34_breakout:.2f} / {v34_relaxed_breakout:.2f}\n'
        msg += f'  📦 量比: {v34_volume:.2f} / {v34_relaxed_volume:.2f}\n\n'
        msg += '💼 V35 參數（嚴格 / 放寬）:\n'
        msg += f'  🏢 營業利益率: {v35_op*100:.1f}% / {v35_relaxed_op*100:.1f}%\n'
        msg += f'  📈 營收YoY: {v35_revenue:.1f}% / {v35_relaxed_revenue:.1f}%\n'
        msg += f'  📦 量比: {v35_volume:.2f} / {v35_relaxed_volume:.2f}\n\n'
        msg += '🧠 AI 參數:\n'
        msg += f'  AI 門檻: {int(ai_threshold*100)}%\n'
        msg += '-' * 30 + '\n'
        msg += '💡 可用指令:\n'
        msg += '• 設定停損 5 (設為5%)\n'
        msg += '• 設定停利 20 (設為20%)\n'
        msg += '• 設定停利 0 (不停利)\n'
        msg += '• 設定信心 60 (AI門檻60%)\n'
        msg += '• 切換積極 / 切換平衡 / 切換寬鬆\n'
        msg += '• V34積極 / V34平衡 / V34寬鬆\n'
        msg += '• V35積極 / V35平衡 / V35寬鬆\n'
        msg += '• 設定V34 18 0.93 0.9\n'
        msg += '• 設定V35 6 0 0.8'
        return msg
    except Exception as exc:
        return f'❌ 讀取設定失敗: {exc}'


def main() -> int:
    print('=' * 60)
    print('[START] Line Bot V3.0 啟動中 (Facade -> app package)')
    print('[MODEL] 模型：按策略動態載入')
    print('[PORT] 伺服器端口: 1688')
    print('=' * 60)

    if os.getenv('LINE_RICH_MENU_AUTO_SYNC', '').strip().lower() in {'1', 'true', 'yes', 'on'}:
        try:
            from core.richmenu import sync_default_rich_menu_from_token

            rich_menu_id = sync_default_rich_menu_from_token()
            print(f'[OK] Rich Menu 綁定完成: {rich_menu_id}')
        except Exception as exc:
            print(f'[WARN] Rich Menu 自動綁定失敗: {exc}')

    app.run(host='0.0.0.0', port=1688, debug=False)
    return 0


from . import web_server as _web_server  # noqa: E402,F401
from .line_bot import callback, handle_message, postback_handler, reply_message  # noqa: E402


__all__ = [
    'app',
    'callback',
    'configuration',
    'current_user',
    'handle_message',
    'handler',
    'login_manager',
    'main',
    'postback_handler',
    'reply_message',
    'strategy_manager',
    'User',
    '_PostbackCache',
    '_apply_news_sentiment_overlay',
    '_build_chip_trend_messages',
    '_build_dashboard_health_check_payload',
    '_build_dashboard_macro_payload',
    '_build_journal_reflection_text',
    '_build_macro_news_messages',
    '_build_market_summary_messages',
    '_build_postback_reply_messages',
    '_build_random_strategy_messages',
    '_compact_command_key',
    '_extract_postback_action',
    '_get_sector_news_summary',
    '_get_stock_mentions_map',
    '_get_stock_specific_news_summary',
    '_is_quick_mode_cmd',
    '_load_backtest_summary_or_error',
    '_load_strategy_candidates',
    '_match_strategy_switch',
    '_normalize_backtest_dates',
    '_normalize_line_text',
    '_parse_news_reason',
    '_postback_cache',
    '_resolve_signal_news_info',
    '_run_portfolio_backtest',
    'get_ngrok_url',
    'get_settings_info',
    'get_strategy_recommendation',
    'get_v30_recommendation',
    'query_stock',
]
