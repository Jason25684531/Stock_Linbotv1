"""LINE command registry: recognition, dispatch, and V34/V35 parameter schema.

Single source of truth for command matching, dispatch priority, and help
text (replaces the previous 450-line if/elif chain in ``line_bot.py``).
No app_pkg / Flask / LINE SDK dependency here — handlers receive a
``LineCommandContext`` carrying whatever they need.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class LineCommandContext:
    event: Any
    msg_text: str
    msg_key: str
    app_pkg: Any
    reply_message: Callable[[str, Any], None]
    reply_stock_diagnosis: Callable[[str, str], bool]


@dataclass(frozen=True)
class LineCommand:
    command_id: str
    matcher: Callable[[LineCommandContext], bool]
    handler: Callable[[LineCommandContext], Optional[str]]
    priority: int
    help_lines: tuple = ()


# ============================================
# V34/V35 strict/relaxed parameter schema
# ============================================


@dataclass(frozen=True)
class ParamField:
    setting_key: str
    min: float
    max: float
    transform: Callable[[float], float] = field(default=lambda v: v)


@dataclass(frozen=True)
class ParamSpec:
    prefix_pattern: str
    match_pattern: str
    fields: tuple
    usage_example: str
    range_error: str
    label: str
    format_detail: Callable[[list], str]


def parse_and_apply(spec: ParamSpec, msg_text: str, update_setting: Callable[[str, str], bool]) -> str:
    value_str = re.sub(spec.prefix_pattern, '', msg_text).strip()
    parts = value_str.split()
    if len(parts) != 3:
        return f'❌ 格式錯誤\n用法：{spec.usage_example}'

    try:
        values = [float(part) for part in parts]
    except ValueError:
        return f'❌ 格式錯誤\n用法：{spec.usage_example}'

    if not all(f.min <= v <= f.max for f, v in zip(spec.fields, values)):
        return spec.range_error

    updates = {f.setting_key: str(f.transform(v)) for f, v in zip(spec.fields, values)}
    ok = all(update_setting(key, value) for key, value in updates.items())
    if not ok:
        return f'❌ {spec.label}參數更新失敗'
    return f'✅ {spec.label}參數已更新\n{spec.format_detail(values)}'


V34_STRICT = ParamSpec(
    prefix_pattern=r'^設定\s*[Vv]34\s+',
    match_pattern=r'^設定\s*[Vv]34\s+[-\d.]+\s+[-\d.]+\s+[-\d.]+\s*$',
    fields=(
        ParamField('v34_revenue_yoy_min', -50, 300),
        ParamField('v34_breakout_ratio', 0.70, 1.10),
        ParamField('v34_volume_ratio_min', 0.0, 5.0),
    ),
    usage_example='設定V34 18 0.93 0.9',
    range_error='❌ 參數超出範圍\nYoY: -50~300, 突破: 0.70~1.10, 量比: 0~5',
    label='V34 嚴格',
    format_detail=lambda v: f'YoY>{v[0]:.1f}% / 突破>={v[1]:.2f} / 量比>{v[2]:.2f}',
)

V34_RELAXED = ParamSpec(
    prefix_pattern=r'^設定\s*[Vv]34\s*放寬\s+',
    match_pattern=r'^設定\s*[Vv]34\s*放寬\s+[-\d.]+\s+[-\d.]+\s+[-\d.]+\s*$',
    fields=(
        ParamField('v34_relaxed_revenue_yoy_min', -50, 300),
        ParamField('v34_relaxed_breakout_ratio', 0.70, 1.10),
        ParamField('v34_relaxed_volume_ratio_min', 0.0, 5.0),
    ),
    usage_example='設定V34放寬 10 0.90 0.7',
    range_error='❌ 參數超出範圍\nYoY: -50~300, 突破: 0.70~1.10, 量比: 0~5',
    label='V34 放寬',
    format_detail=lambda v: f'YoY>{v[0]:.1f}% / 突破>={v[1]:.2f} / 量比>{v[2]:.2f}',
)

V35_STRICT = ParamSpec(
    prefix_pattern=r'^設定\s*[Vv]35\s+',
    match_pattern=r'^設定\s*[Vv]35\s+[-\d.]+\s+[-\d.]+\s+[-\d.]+\s*$',
    fields=(
        ParamField('v35_op_margin_min', -20, 100, transform=lambda v: v / 100),
        ParamField('v35_revenue_yoy_min', -100, 300),
        ParamField('v35_volume_ratio_min', 0.0, 5.0),
    ),
    usage_example='設定V35 6 0 0.8',
    range_error='❌ 參數超出範圍\n利益率%: -20~100, YoY: -100~300, 量比: 0~5',
    label='V35 嚴格',
    format_detail=lambda v: f'利益率>{v[0]:.1f}% / YoY>{v[1]:.1f}% / 量比>{v[2]:.2f}',
)

V35_RELAXED = ParamSpec(
    prefix_pattern=r'^設定\s*[Vv]35\s*放寬\s+',
    match_pattern=r'^設定\s*[Vv]35\s*放寬\s+[-\d.]+\s+[-\d.]+\s+[-\d.]+\s*$',
    fields=(
        ParamField('v35_relaxed_op_margin_min', -20, 100, transform=lambda v: v / 100),
        ParamField('v35_relaxed_revenue_yoy_min', -100, 300),
        ParamField('v35_relaxed_volume_ratio_min', 0.0, 5.0),
    ),
    usage_example='設定V35放寬 4 -5 0.6',
    range_error='❌ 參數超出範圍\n利益率%: -20~100, YoY: -100~300, 量比: 0~5',
    label='V35 放寬',
    format_detail=lambda v: f'利益率>{v[0]:.1f}% / YoY>{v[1]:.1f}% / 量比>{v[2]:.2f}',
)


def _param_command(command_id: str, spec: ParamSpec, priority: int, help_lines: tuple = ()) -> LineCommand:
    return LineCommand(
        command_id=command_id,
        matcher=lambda ctx: bool(re.match(spec.match_pattern, ctx.msg_text)),
        handler=lambda ctx: parse_and_apply(spec, ctx.msg_text, ctx.app_pkg.update_setting),
        priority=priority,
        help_lines=help_lines,
    )


# ============================================
# Non-parameter command handlers
# ============================================

_QUICK_MODE_COMBOS = tuple(
    (ver, style) for ver in ('34', '35') for style in ('aggressive', 'balanced', 'loose', 'conservative')
)
_QUICK_MODE_LABELS = {
    'aggressive': '積極',
    'balanced': '平衡',
    'loose': '寬鬆',
    'conservative': '保守',
}


def _handle_mode_switch(ctx: LineCommandContext) -> str:
    app_pkg = ctx.app_pkg
    mode_label, preset_key = app_pkg.MODE_CMD_MAP[ctx.msg_key]
    emoji = app_pkg.MODE_EMOJI.get(preset_key, '')
    if not app_pkg.update_setting('mode', preset_key):
        return '❌ 切換失敗，請稍後再試'
    updates = {**app_pkg.V34_MODE_PRESETS[preset_key], **app_pkg.V35_MODE_PRESETS[preset_key]}
    if app_pkg._apply_settings_batch(updates):
        return f"{emoji} {app_pkg.MODE_REPLY_TEMPLATE[preset_key]}\n💡 直接輸入「推薦」即可生效"
    return f'⚠️ 模式已切換至{mode_label}，但部分 V34/V35 參數更新失敗'


def _handle_quick_mode(ctx: LineCommandContext) -> str:
    app_pkg = ctx.app_pkg
    for ver, style in _QUICK_MODE_COMBOS:
        if app_pkg._is_quick_mode_cmd(ctx.msg_text, ver, style):
            presets = app_pkg.V34_MODE_PRESETS if ver == '34' else app_pkg.V35_MODE_PRESETS
            all_ok = app_pkg._apply_settings_batch(presets[style])
            emoji = app_pkg.MODE_EMOJI.get(style, '')
            style_label = _QUICK_MODE_LABELS[style]
            if all_ok:
                return f'{emoji} V{ver} 已設為{style_label}檔位'
            return f'❌ V{ver} {style_label}設定失敗'
    return None  # pragma: no cover - matcher guarantees a hit


def _handle_set_confidence(ctx: LineCommandContext) -> str:
    app_pkg = ctx.app_pkg
    try:
        value_str = ctx.msg_text.replace('設定信心', '').strip()
        value = float(value_str) / 100
        is_valid, err_msg = app_pkg.validate_setting('ai_threshold', str(value))
        if not is_valid:
            return f'❌ {err_msg}\n範例：設定信心 60（代表60%）'
        if app_pkg.update_setting('ai_threshold', str(value)):
            return f'🧠 AI 信心門檻已設為 {int(value*100)}%\n將只推薦高於此門檻的股票'
        return '❌ 設定失敗'
    except ValueError:
        return '❌ 格式錯誤\n正確用法：設定信心 60'


def _handle_set_stop_loss(ctx: LineCommandContext) -> str:
    app_pkg = ctx.app_pkg
    try:
        value_str = ctx.msg_text.replace('設定停損', '').strip()
        value = float(value_str) / 100
        if 0.01 <= value <= 0.20:
            if app_pkg.update_setting('v30_stop_loss', str(value)):
                return f'🛡️ V30停損已設為 {int(value*100)}%\n下次選股將使用新參數'
            return '❌ 設定失敗'
        return '❌ 停損需在 1%-20% 之間\n範例：設定停損 5'
    except ValueError:
        return '❌ 格式錯誤\n正確用法：設定停損 5（代表5%）'


def _handle_set_take_profit(ctx: LineCommandContext) -> str:
    app_pkg = ctx.app_pkg
    try:
        value_str = ctx.msg_text.replace('設定停利', '').strip()
        if value_str == '0' or value_str.lower() == '不停利':
            if app_pkg.update_setting('v30_take_profit', '0.0'):
                params = app_pkg.get_v30_params_from_db()
                return f'🎯 V30停利已取消\n將持有至停損或到期（{params["MAX_HOLD_DAYS"]}天）'
            return '❌ 設定失敗'
        value = float(value_str) / 100
        if 0.05 <= value <= 0.50:
            if app_pkg.update_setting('v30_take_profit', str(value)):
                return f'🎯 V30停利已設為 {int(value*100)}%\n下次選股將使用新參數'
            return '❌ 設定失敗'
        return '❌ 停利需在 5%-50% 之間\n範例：設定停利 20（代表20%）\n或輸入「設定停利 0」取消停利'
    except ValueError:
        return '❌ 格式錯誤\n用法：\n• 設定停利 20（20%停利）\n• 設定停利 0（不停利）'


def _handle_view_settings(ctx: LineCommandContext) -> str:
    return ctx.app_pkg.get_settings_info()


def _handle_strategy_switch(ctx: LineCommandContext) -> str:
    app_pkg = ctx.app_pkg
    strategy_key, strategy_display, features_text = app_pkg._match_strategy_switch(ctx.msg_text.lower())
    try:
        mgr = app_pkg.StrategyManager()
        if mgr.set_active_strategy(strategy_key):
            return f'🔄 已切換至【{strategy_display}】\n\n🎯 特色：\n{features_text}\n💡 輸入「推薦」開始選股'
        return '❌ 切換失敗，請稍後再試'
    except Exception as exc:
        return f'❌ 切換失敗: {exc}'


def _handle_view_strategy(ctx: LineCommandContext) -> str:
    app_pkg = ctx.app_pkg
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
        reply += '• 切換V38 → 品質價值低波動策略\n'
        return reply
    except Exception as exc:
        return f'❌ 查詢策略失敗: {exc}'


def _handle_v30(ctx: LineCommandContext) -> str:
    return ctx.app_pkg.get_v30_recommendation()


def _handle_recommend(ctx: LineCommandContext) -> Optional[str]:
    app_pkg = ctx.app_pkg
    try:
        flex_msg = app_pkg.get_strategy_recommendation(as_flex=True)
        if isinstance(flex_msg, str):
            return flex_msg
        ctx.reply_message(ctx.event.reply_token, [flex_msg])
        return None
    except Exception as exc:
        print(f'⚠️ Flex Carousel 建構失敗，降級為純文字: {exc}')
        return app_pkg.get_strategy_recommendation(as_flex=False)


def _handle_strategy_picker(ctx: LineCommandContext) -> None:
    messages = ctx.app_pkg._build_strategy_picker_messages()
    ctx.reply_message(ctx.event.reply_token, messages)
    return None


def _handle_stock_code(ctx: LineCommandContext) -> None:
    ctx.reply_stock_diagnosis(ctx.event.reply_token, ctx.msg_text)
    return None


def _handle_query_stock(ctx: LineCommandContext) -> str:
    stock_id = ctx.msg_text.replace('查詢', '').strip()
    if stock_id.isdigit():
        return ctx.app_pkg.query_stock(stock_id)
    return '❌ 請輸入正確的股票代號'


def _handle_holdings(ctx: LineCommandContext) -> Optional[str]:
    app_pkg = ctx.app_pkg
    import pandas as pd

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
            ctx.reply_message(ctx.event.reply_token, [flex_msg])
            return None
        return '📭 目前無 AI 持股\n輸入「推薦」取得今日選股建議'
    except Exception as exc:
        print(f'⚠️ 持股查詢失敗: {exc}')
        return '📭 目前無持股紀錄\n輸入「推薦」取得今日選股建議'


def _handle_journal(ctx: LineCommandContext) -> None:
    ctx.reply_message(ctx.event.reply_token, ctx.app_pkg._build_journal_reflection_messages())
    return None


def _handle_backtest(ctx: LineCommandContext) -> Optional[str]:
    app_pkg = ctx.app_pkg
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
            ctx.reply_message(ctx.event.reply_token, [flex_msg])
            return None
        return '📊 尚無回測數據\n請先執行 python jobs/run_backtest.py'
    except Exception as exc:
        print(f'⚠️ 回測摘要失敗: {exc}')
        return '📊 尚無回測數據\n請先執行 python jobs/run_backtest.py'


def _handle_dashboard(ctx: LineCommandContext) -> str:
    app_pkg = ctx.app_pkg
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
    return reply


def _handle_news(ctx: LineCommandContext) -> Optional[str]:
    app_pkg = ctx.app_pkg
    import datetime as _dt
    import traceback

    try:
        from core.news_agent import get_morning_news_summary

        news_summary = get_morning_news_summary()
        today_str = _dt.datetime.now().strftime('%Y-%m-%d')
        news_flex = app_pkg.create_news_flex(news_summary, today_str)
        picks_flex = app_pkg.get_strategy_recommendation(as_flex=True)

        messages = [news_flex]
        if not isinstance(picks_flex, str):
            messages.append(picks_flex)

        ctx.reply_message(ctx.event.reply_token, messages)
        return None
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
            return reply
        except Exception as inner_exc:
            return f'❌ 新聞取得失敗: {str(inner_exc)[:100]}'


# ============================================
# Registry (priority == original if/elif order)
# ============================================

REGISTRY = (
    LineCommand(
        command_id='mode_switch',
        matcher=lambda ctx: ctx.msg_key in ctx.app_pkg.MODE_CMD_MAP,
        handler=_handle_mode_switch,
        priority=10,
        help_lines=('• 切換積極 / 切換平衡 / 切換寬鬆\n',),
    ),
    LineCommand(
        command_id='quick_mode',
        matcher=lambda ctx: any(
            ctx.app_pkg._is_quick_mode_cmd(ctx.msg_text, ver, style) for ver, style in _QUICK_MODE_COMBOS
        ),
        handler=_handle_quick_mode,
        priority=20,
        help_lines=('• V34積極 / V34平衡 / V34寬鬆\n', '• V35積極 / V35平衡 / V35寬鬆\n'),
    ),
    _param_command('v34_relaxed', V34_RELAXED, 30, help_lines=('• 設定V34放寬 10 0.90 0.7\n',)),
    _param_command('v34_strict', V34_STRICT, 40, help_lines=('• 設定V34 18 0.93 0.9\n',)),
    _param_command('v35_relaxed', V35_RELAXED, 50, help_lines=('• 設定V35放寬 4 -5 0.6\n',)),
    _param_command('v35_strict', V35_STRICT, 60, help_lines=('• 設定V35 6 0 0.8\n',)),
    LineCommand(
        command_id='set_confidence',
        matcher=lambda ctx: ctx.msg_text.startswith('設定信心'),
        handler=_handle_set_confidence,
        priority=70,
    ),
    LineCommand(
        command_id='set_stop_loss',
        matcher=lambda ctx: ctx.msg_text.startswith('設定停損'),
        handler=_handle_set_stop_loss,
        priority=80,
        help_lines=('• 設定停損 5 (停損5%)\n',),
    ),
    LineCommand(
        command_id='set_take_profit',
        matcher=lambda ctx: ctx.msg_text.startswith('設定停利'),
        handler=_handle_set_take_profit,
        priority=90,
        help_lines=('• 設定停利 20 (停利20%)\n', '• 設定停利 0 (不停利)\n'),
    ),
    LineCommand(
        command_id='view_settings',
        matcher=lambda ctx: ctx.msg_text == '查看設定',
        handler=_handle_view_settings,
        priority=100,
        help_lines=('• 查看設定\n',),
    ),
    LineCommand(
        command_id='strategy_switch',
        matcher=lambda ctx: bool(ctx.app_pkg._match_strategy_switch(ctx.msg_text.lower())),
        handler=_handle_strategy_switch,
        priority=110,
        help_lines=(
            '• 切換V30 → V31 混合（均衡型）\n',
            '• 切換V33 → 低波動（穩健型）\n',
            '• 切換V34 → 雙渦輪（積極型）\n',
            '• 切換V35 → 經營效益（成長型）\n',
            '• 切換V36 → 籌碼動能（追蹤型）\n',
            '• 切換V37 → 均值回歸（反轉型）\n',
            '• 切換V38 → 品質價值低波動策略\n',
        ),
    ),
    LineCommand(
        command_id='view_strategy',
        matcher=lambda ctx: ctx.msg_text in ('查看策略', '目前策略', '策略狀態'),
        handler=_handle_view_strategy,
        priority=120,
        help_lines=('• 查看策略 → 顯示目前策略詳情\n',),
    ),
    LineCommand(
        command_id='v30',
        matcher=lambda ctx: ctx.msg_text in ('V30', 'v30', '策略'),
        handler=_handle_v30,
        priority=130,
        help_lines=('• V30 → 純技術分析選股\n',),
    ),
    LineCommand(
        command_id='recommend',
        matcher=lambda ctx: ctx.msg_text in ('推薦', '選股', 'AI'),
        handler=_handle_recommend,
        priority=140,
        help_lines=('• 推薦 → 使用當前策略選出前5名\n',),
    ),
    LineCommand(
        command_id='strategy_picker',
        matcher=lambda ctx: ctx.msg_text in ('策略選股', '策略挑選', '選策略'),
        handler=_handle_strategy_picker,
        priority=150,
    ),
    LineCommand(
        command_id='stock_code',
        matcher=lambda ctx: ctx.msg_text.isdigit() and len(ctx.msg_text) == 4,
        handler=_handle_stock_code,
        priority=160,
        help_lines=('• 2330 → 個股 AI 健康診斷\n',),
    ),
    LineCommand(
        command_id='query_stock',
        matcher=lambda ctx: ctx.msg_text.startswith('查詢'),
        handler=_handle_query_stock,
        priority=170,
    ),
    LineCommand(
        command_id='holdings',
        matcher=lambda ctx: ctx.msg_text in ('持股', '目前持股', 'AI持股', '庫存'),
        handler=_handle_holdings,
        priority=180,
        help_lines=('• 持股 → 查看 AI 模擬持股\n',),
    ),
    LineCommand(
        command_id='journal',
        matcher=lambda ctx: ctx.msg_text in ('日誌', '反思', 'journal'),
        handler=_handle_journal,
        priority=190,
    ),
    LineCommand(
        command_id='backtest',
        matcher=lambda ctx: ctx.msg_text in ('回測', '績效', '回測結果', 'backtest'),
        handler=_handle_backtest,
        priority=200,
        help_lines=('• 回測 → 最新回測績效摘要\n',),
    ),
    LineCommand(
        command_id='dashboard',
        matcher=lambda ctx: ctx.msg_text in ('dashboard', '儀表板', 'Dashboard', '看板'),
        handler=_handle_dashboard,
        priority=210,
        help_lines=('• dashboard → 開啟視覺化儀表板\n',),
    ),
    LineCommand(
        command_id='news',
        matcher=lambda ctx: ctx.msg_text in ('新聞', 'News', 'news', '早報'),
        handler=_handle_news,
        priority=220,
        help_lines=('• 新聞 → 今日新聞摘要 + 選股推薦\n',),
    ),
)


def dispatch(ctx: LineCommandContext) -> Optional[LineCommand]:
    """Return the first matching command in priority order, or None."""
    for command in sorted(REGISTRY, key=lambda c: c.priority):
        if command.matcher(ctx):
            return command
    return None


# ============================================
# Help message (generated from registry metadata)
# ============================================

_HELP_SECTIONS = (
    ('🎯 選股功能', ('recommend', 'news', 'v30', 'stock_code', 'holdings', 'backtest')),
    ('🔄 策略切換', ('strategy_switch', 'view_strategy')),
    ('📊 Dashboard', ('dashboard',)),
    ('⚙️ V30 參數調整', ('set_stop_loss', 'set_take_profit', 'view_settings')),
    ('🚀 V34/V35 參數調整', ('mode_switch', 'quick_mode', 'v34_strict', 'v34_relaxed', 'v35_strict', 'v35_relaxed')),
)


def build_help_message(app_pkg) -> str:
    by_id = {command.command_id: command for command in REGISTRY}
    try:
        mgr = app_pkg.StrategyManager()
        current = mgr.get_active_strategy()
        current_name = current.display_name if current else 'V31 混合策略'
    except Exception:
        current_name = '未知'

    lines = ['🤖 【StockAI Line Bot V3.0】\n']
    lines.append(f'📌 目前策略：{current_name}\n')
    lines.append('\n📋 指令清單:\n')
    lines.append('-' * 30 + '\n')

    for index, (section_title, command_ids) in enumerate(_HELP_SECTIONS):
        prefix = '' if index == 0 else '\n'
        lines.append(f'{prefix}【{section_title}】\n')
        for command_id in command_ids:
            lines.extend(by_id[command_id].help_lines)

    lines.append('-' * 30 + '\n')
    lines.append('💡 輸入「推薦」開始選股\n')
    lines.append('⚠️ 所有推薦僅供參考')
    return ''.join(lines)
