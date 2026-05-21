"""Canonical Flask web entrypoint and dashboard routes."""

from __future__ import annotations

import os
import json
import sys
import traceback

import pandas as pd
from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import text

from config import Config, V34_MODE_PRESETS, V35_MODE_PRESETS
from core import db_helper
from . import app

app_pkg = sys.modules[__package__]


def _parse_csv_query_values(*keys: str) -> list[str] | None:
    values: list[str] = []
    seen: set[str] = set()
    for key in keys:
        for raw_value in request.args.getlist(key):
            for candidate in str(raw_value or '').split(','):
                normalized = str(candidate or '').strip().lower()
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                values.append(normalized)
    return values or None


def _dashboard_json_response(payload: dict[str, object], status_code: int = 200):
    sanitized = app_pkg._sanitize_dashboard_json(payload)
    return app.response_class(
        json.dumps(sanitized, ensure_ascii=False, allow_nan=False),
        status=status_code,
        mimetype='application/json',
    )


@app.route(Config.APP_HEALTH_PATH)
def health_check():
    """提供 compose readiness 使用的輕量健康檢查。"""
    checks = [
        {'component': 'flask', 'status': 'ok'},
        {
            'component': 'mcp_config',
            'status': 'ok' if Config.MCP_BASE_URL else 'missing',
        },
    ]
    return jsonify(
        {
            'status': 'ok',
            'service': 'stock_bot',
            'version': '0.1.0',
            'checks': checks,
        }
    ), 200


@app.route('/login', methods=['GET', 'POST'])
def login():
    """登入頁面。"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        if app_pkg.User.validate_password(password):
            user = app_pkg.User('admin')
            login_user(user)
            flash('✅ 登入成功！', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard'))
        flash('❌ 密碼錯誤，請重試', 'error')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    """登出。"""
    logout_user()
    flash('👋 已登出', 'success')
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    """首頁重定向到 Dashboard。"""
    return redirect(url_for('dashboard'))


@app.route('/dashboard')
@login_required
def dashboard():
    """Dashboard 主頁面。"""
    active_strategies = app_pkg.strategy_manager.get_active_strategy_names()
    strategy_options = app_pkg.strategy_manager.list_strategies()
    return render_template(
        'dashboard.html',
        active_strategies=active_strategies,
        strategy_options=strategy_options,
        current_strategy=active_strategies[0] if active_strategies else 'v31_hybrid',
        current_mode=str(app_pkg.get_setting('mode', 'balanced')),
    )


@app.route('/update_strategy', methods=['POST'])
@login_required
def update_strategy():
    """切換策略 (V2: 支援多策略)。"""
    try:
        selected_strategies = request.form.getlist('strategies')
        if not selected_strategies:
            flash('請至少選擇一個策略', 'error')
            return redirect(url_for('dashboard'))

        success = app_pkg.strategy_manager.set_active_strategies(selected_strategies)
        if success:
            if len(selected_strategies) == 1:
                strategy_obj = app_pkg.strategy_manager.get_active_strategy()
                flash(f'✅ 已切換至 {strategy_obj.display_name}', 'success')
            else:
                flash(f'✅ 已啟用 {len(selected_strategies)} 個策略', 'success')
            print(f'[Strategy] 切換至: {selected_strategies}')
        else:
            flash('❌ 策略切換失敗', 'error')
    except Exception as exc:
        flash(f'❌ 切換失敗: {str(exc)}', 'error')
        print(f'[ERROR] 策略切換失敗: {exc}')

    return redirect(url_for('dashboard'))


@app.route('/update_mode', methods=['POST'])
@login_required
def update_mode():
    """切換 V34/V35 共用模式：積極 / 平衡 / 寬鬆。"""
    try:
        mode_map = {
            'aggressive': ('積極', 'aggressive'),
            'balanced': ('平衡', 'balanced'),
            'loose': ('寬鬆', 'loose'),
            'conservative': ('穩健', 'conservative'),
        }
        req_mode = (request.form.get('mode') or '').strip().lower()
        if req_mode not in mode_map:
            flash('❌ 未知模式，請使用 積極/平衡/寬鬆', 'error')
            return redirect(url_for('dashboard'))

        mode_label, preset_key = mode_map[req_mode]
        updates = {**V34_MODE_PRESETS[preset_key], **V35_MODE_PRESETS[preset_key]}
        ok_mode = app_pkg.update_setting('mode', req_mode)
        ok_params = app_pkg._apply_settings_batch(updates)

        if ok_mode and ok_params:
            flash(f'✅ 已切換至【{mode_label}模式】（V34/V35 同步更新）', 'success')
        elif ok_mode:
            flash(f'⚠️ 已切換模式，但部分參數更新失敗（{mode_label}）', 'error')
        else:
            flash('❌ 模式切換失敗', 'error')
    except Exception as exc:
        flash(f'❌ 模式切換異常: {exc}', 'error')

    return redirect(url_for('dashboard'))


@app.route('/favicon.ico')
def favicon():
    return '', 204


@app.route('/api/performance')
def api_performance():
    """取得回測資產曲線數據。"""
    try:
        db_curve = app_pkg.get_backtest_equity_curve()
        if db_curve.get('dates'):
            return jsonify(db_curve)

        profit_file = 'ML_Data/backtest_profit_report.csv'
        if not os.path.exists(profit_file):
            return jsonify({'error': '回測數據不存在，請先執行 jobs/run_backtest.py'}), 404

        df = pd.read_csv(profit_file)
        return jsonify(
            {
                'dates': df['date'].tolist(),
                'equity': df['asset_value'].tolist(),
                'roi': df['roi'].tolist(),
            }
        )
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/trades')
def api_trades():
    """取得交易明細（最近 50 筆）。"""
    try:
        db_trades = app_pkg.get_recent_backtest_trades(limit=50)
        if db_trades:
            return jsonify(db_trades)

        trades_file = 'ML_Data/backtest_result.csv'
        if not os.path.exists(trades_file):
            return jsonify({'error': '交易數據不存在，請先執行 jobs/run_backtest.py'}), 404

        df = pd.read_csv(trades_file)
        if 'strategy' not in df.columns:
            df['strategy'] = 'unknown'

        df_recent = df.tail(50)
        df_recent = df_recent.where(pd.notnull(df_recent), None)
        trades = df_recent.to_dict('records')
        return jsonify(trades)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/summary')
def api_summary():
    """取得回測摘要統計。"""
    try:
        summary, error_response = app_pkg._load_backtest_summary_or_error('回測數據不存在')
        if error_response:
            return error_response
        return jsonify(app_pkg._build_summary_response(summary))
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/user/trade', methods=['POST'])
def api_user_trade():
    """記錄使用者模擬交易。"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        stock_id = data.get('stock_id')
        buy_price = data.get('buy_price')
        buy_date = data.get('buy_date')

        if not all([user_id, stock_id, buy_price, buy_date]):
            return jsonify({'error': '缺少必要參數'}), 400

        if not app_pkg.create_user_simulation_trade(
            user_id=user_id,
            stock_id=stock_id,
            buy_price=buy_price,
            buy_date=buy_date,
        ):
            return jsonify({'error': '資料庫寫入失敗'}), 500

        return jsonify({'success': True, 'message': '模擬交易記錄成功'})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/pk/battle', methods=['GET'])
def api_pk_battle():
    """取得人機對決統計數據。"""
    try:
        user_roi = 15.5
        ai_roi = 19.2

        trades_file = 'ML_Data/backtest_result.csv'
        if os.path.exists(trades_file):
            df_trades = pd.read_csv(trades_file)
            if 'roi' in df_trades.columns and not df_trades.empty:
                ai_roi = df_trades['roi'].mean()
                ai_win_rate = (df_trades['roi'] > 0).mean() * 100
            else:
                ai_win_rate = 50
        else:
            ai_win_rate = 50

        user_win_rate = 45
        return jsonify(
            {
                'user_roi': round(user_roi, 2),
                'ai_roi': round(ai_roi, 2),
                'user_win_rate': round(user_win_rate, 2),
                'ai_win_rate': round(ai_win_rate, 2),
            }
        )
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/strategies')
def api_strategies():
    """取得所有策略清單及當前啟用策略。"""
    try:
        mgr = app_pkg.StrategyManager()
        available = mgr.list_available_strategies()
        active_names = mgr.get_active_strategy_names()

        strategies = []
        for name, display in available.items():
            try:
                strategy = mgr._get_or_load_strategy(name)
                info = strategy.get_strategy_info() if hasattr(strategy, 'get_strategy_info') else {}
                strategies.append(
                    {
                        'name': name,
                        'display_name': display,
                        'type': info.get('type', ''),
                        'risk_level': info.get('risk_level', ''),
                        'description': info.get('description', strategy.description if hasattr(strategy, 'description') else ''),
                        'active': name in active_names,
                    }
                )
            except Exception:
                strategies.append(
                    {
                        'name': name,
                        'display_name': display,
                        'active': name in active_names,
                    }
                )

        return jsonify({'strategies': strategies, 'active': active_names, 'count': len(strategies)})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/daily-signals')
def api_daily_signals():
    """取得今日選股訊號。"""
    try:
        requested_strategy = (request.args.get('strategy') or '').strip()
        try:
            top_n = int(request.args.get('top_n', 5))
        except (TypeError, ValueError):
            top_n = 5
        top_n = max(1, min(top_n, 20))

        strategy_alias = {
            'v31': 'v31_hybrid',
            'v33': 'v33_low_vol',
            'v34': 'v34_turbo',
            'v35': 'v35_innovation',
            'v36': 'v36_chip_momentum',
            'v37': 'v37_mean_reversion',
            'v38': 'v38_value_dividend',
        }

        requested_date = app_pkg._current_line_date()
        baseline_date = app_pkg._resolve_ui_baseline_date()
        df, date_str = app_pkg.get_stock_data(date_str=baseline_date) if baseline_date else app_pkg.get_stock_data()
        if df.empty:
            return jsonify(
                {
                    'error': '無最新資料',
                    'date': None,
                    'strategy_key': None,
                    'strategy_display': None,
                    'top_n': top_n,
                    'signals': [],
                }
            )

        df = app_pkg.supplement_financial_data(df)

        try:
            mgr = app_pkg.StrategyManager()
            if requested_strategy:
                key = requested_strategy.lower().strip()
                strategy_key = strategy_alias.get(key, key)
                active = mgr.get_strategy(strategy_key)
                if active is None:
                    return jsonify(
                        {
                            'error': f'無效策略: {requested_strategy}',
                            'date': date_str,
                            'strategy_key': strategy_key,
                            'strategy_display': None,
                            'top_n': top_n,
                            'signals': [],
                        }
                    ), 400
            else:
                active = mgr.get_active_strategy()
                names = mgr.get_active_strategy_names()
                strategy_key = names[0] if names else 'v31_hybrid'

            strategy_name = active.display_name
            candidates, fallback_meta, has_persisted = app_pkg._load_strategy_candidates(
                active=active,
                strategy_key=strategy_key,
                market_df=df,
                requested_date=requested_date,
                limit=top_n,
            )

            if candidates.empty:
                return jsonify(
                    {
                        'date': fallback_meta.get('recommendation_date') or date_str,
                        'requested_date': fallback_meta.get('requested_date') or date_str,
                        'market_anchor_date': fallback_meta.get('market_anchor_date') or date_str,
                        'recommendation_date': fallback_meta.get('recommendation_date') or date_str,
                        'resolution_source': fallback_meta.get('resolution_source', 'missing'),
                        'has_persisted_snapshot': fallback_meta.get('has_persisted_snapshot', False),
                        'strategy_key': strategy_key,
                        'strategy_display': strategy_name,
                        'top_n': top_n,
                        'fallback_used': fallback_meta.get('fallback_used', False),
                        'market_warning': app_pkg.format_market_fallback_notice(fallback_meta, strategy_name),
                        'signals': [],
                        'message': f'今日 {strategy_name} 無符合條件的股票',
                    }
                )

            picks = candidates.head(top_n)
            active_strategy = active
        except Exception:
            picks = app_pkg.get_best_stocks_v31_hybrid(df, top_n=top_n)
            strategy_key = 'v31_hybrid'
            strategy_name = 'V31 混合策略'
            active_strategy = None
            fallback_meta = {}

        if picks.empty:
            return jsonify(
                {
                    'date': date_str,
                    'strategy_key': strategy_key,
                    'strategy_display': strategy_name,
                    'top_n': top_n,
                    'signals': [],
                    'message': '今日無符合條件的股票',
                }
            )

        signals = []
        signal_date = fallback_meta.get('recommendation_date') or date_str
        with app_pkg._live_signal_news_timeout_scope():
            try:
                stock_mentions_map = app_pkg._get_stock_mentions_map([str(sid) for sid in picks['stock_id'].tolist()])
            except Exception as news_exc:
                print(f'⚠️ /api/daily-signals 個股新聞讀取失敗: {news_exc}')
                stock_mentions_map = {}

            for _, row in picks.iterrows():
                close_price = float(row['close_price'])
                market_anchor_date = app_pkg.normalize_date_str(
                    fallback_meta.get('market_anchor_date') if fallback_meta else date_str
                )
                price_trade_date = app_pkg.normalize_date_str(
                    row.get('price_trade_date')
                    or row.get('market_trade_date')
                    or signal_date
                )
                recommendation_trade_date = app_pkg.normalize_date_str(
                    row.get('recommendation_trade_date')
                    or fallback_meta.get('recommendation_date')
                    or signal_date
                )
                recommendation_close_price = app_pkg.safe_float(
                    row.get('recommendation_close_price')
                )
                if recommendation_close_price is None:
                    recommendation_close_price = close_price
                price_is_stale = bool(
                    price_trade_date
                    and market_anchor_date
                    and price_trade_date < market_anchor_date
                )
                recommendation_is_stale = bool(
                    recommendation_trade_date
                    and market_anchor_date
                    and recommendation_trade_date < market_anchor_date
                )
                stop_loss_rate = float(getattr(active_strategy, 'stop_loss', Config.V30_STOP_LOSS)) if active_strategy else Config.V30_STOP_LOSS
                take_profit_rate = float(getattr(active_strategy, 'take_profit', Config.V30_TAKE_PROFIT)) if active_strategy else Config.V30_TAKE_PROFIT
                try:
                    news_info = app_pkg._resolve_signal_news_info(row, signal_date, stock_mentions_map)
                except Exception as news_exc:
                    print(f"⚠️ /api/daily-signals {row.get('stock_id')} 新聞摘要失敗: {news_exc}")
                    news_info = app_pkg._parse_news_reason(row.get('news_boost_reason') or '')

                signal = {
                    'stock_id': row['stock_id'],
                    'close_price': close_price,
                    'price_trade_date': price_trade_date,
                    'price_source_date': price_trade_date,
                    'price_basis': str(row.get('price_basis') or 'raw_close'),
                    'price_data_source': str(row.get('price_data_source') or 'daily_recommendations'),
                    'price_is_stale': price_is_stale,
                    'recommendation_close_price': recommendation_close_price,
                    'recommendation_trade_date': recommendation_trade_date,
                    'recommendation_price_basis': str(row.get('recommendation_price_basis') or 'raw_close'),
                    'recommendation_data_source': str(row.get('recommendation_data_source') or 'daily_recommendations'),
                    'recommendation_is_stale': recommendation_is_stale,
                    'strategy': strategy_name,
                    'strategy_key': strategy_key,
                    'ai_score': app_pkg.safe_float(row.get('ai_score')) if 'ai_score' in row else None,
                    'rsi': app_pkg.safe_float(row.get('rsi')) if 'rsi' in row else None,
                    'volume': app_pkg.safe_int(row.get('volume')) if 'volume' in row else None,
                    'ma20': app_pkg.safe_float(row.get('ma20')) if 'ma20' in row else None,
                    'ma60': app_pkg.safe_float(row.get('ma60')) if 'ma60' in row else None,
                    'bias': app_pkg.safe_float(row.get('bias')) if 'bias' in row else None,
                    'op_profit_margin': app_pkg.safe_float(row.get('op_profit_margin')) if 'op_profit_margin' in row else None,
                    'revenue_yoy': app_pkg.safe_float(row.get('revenue_yoy')) if 'revenue_yoy' in row else None,
                    'chip_score': app_pkg.safe_float(row.get('chip_score')) if 'chip_score' in row else None,
                    'foreign_buy': app_pkg.safe_int(row.get('foreign_buy')) if 'foreign_buy' in row else None,
                    'news_boost_reason': news_info['raw'],
                    'news_reason_items': news_info['items'],
                    'news_signal_title': news_info['title'],
                    'news_is_bearish': news_info['is_bearish'],
                    'suggested_buy_price': round(close_price, 2),
                    'suggested_sell_price': round(close_price * (1 + take_profit_rate), 2),
                    'suggested_stop_loss_price': round(close_price * (1 - stop_loss_rate), 2),
                    'detail_url': f"https://goodinfo.tw/tw/StockDetail.asp?STOCK_ID={row['stock_id']}",
                }
                signals.append(signal)

        return jsonify(
            {
                'date': signal_date,
                'requested_date': fallback_meta.get('requested_date') if fallback_meta else date_str,
                'market_anchor_date': fallback_meta.get('market_anchor_date') if fallback_meta else date_str,
                'recommendation_date': fallback_meta.get('recommendation_date') if fallback_meta else signal_date,
                'resolution_source': fallback_meta.get('resolution_source', 'missing') if fallback_meta else 'missing',
                'has_persisted_snapshot': fallback_meta.get('has_persisted_snapshot', False) if fallback_meta else False,
                'strategy_key': strategy_key,
                'strategy_display': strategy_name,
                'top_n': top_n,
                'fallback_used': fallback_meta.get('fallback_used', False) if fallback_meta else False,
                'market_warning': app_pkg.format_market_fallback_notice(fallback_meta, strategy_name) if fallback_meta else '',
                'signals': signals,
                'count': len(signals),
            }
        )
    except Exception as exc:
        traceback.print_exc()
        return jsonify({'error': str(exc)}), 500


@app.route('/api/news_sentiment')
@login_required
def api_news_sentiment():
    """回傳指定日期（或最新）消息面情緒摘要。"""
    date_str = request.args.get('date')
    data = app_pkg.get_news_sentiment(date_str)
    return jsonify(data)


@app.route('/api/dashboard/health-check')
def api_dashboard_health_check():
    """回傳 dashboard beta 個股健檢 payload。"""
    stock_id = str(request.args.get('symbol') or request.args.get('stock_id') or '').strip()
    if not stock_id:
        return jsonify({'error': '缺少股票代號 symbol'}), 400

    try:
        payload = app_pkg._build_dashboard_health_check_payload(
            stock_id=stock_id,
            requested_date=request.args.get('date'),
            period=request.args.get('period'),
            overlays=_parse_csv_query_values('overlay', 'overlays'),
            panes=_parse_csv_query_values('pane', 'panes', 'panel', 'panels'),
        )
        status_code = 200 if payload.get('status') != 'error' else 500
        return _dashboard_json_response(payload, status_code)
    except Exception as exc:
        traceback.print_exc()
        return jsonify({'error': str(exc)}), 500


def _build_stock_analysis_payload(health_payload: dict[str, object], stock_id: str) -> dict[str, object]:
    series = health_payload.get('series') if isinstance(health_payload.get('series'), dict) else {}
    rule_report = health_payload.get('rule_report') if isinstance(health_payload.get('rule_report'), dict) else {}
    war_room = health_payload.get('war_room') if isinstance(health_payload.get('war_room'), dict) else {}

    payload = {
        'status': health_payload.get('status', 'empty'),
        'as_of_date': health_payload.get('as_of_date'),
        'stock_id': health_payload.get('symbol') or health_payload.get('stock_id') or stock_id,
        'source': health_payload.get('source') or ['daily_market_data'],
        'warnings': health_payload.get('warnings') or [],
        'quote': health_payload.get('quote') or {},
        'kline_ma': {
            'candles': series.get('candles') or [],
            'volume': series.get('volume') or [],
            'ma5': series.get('ma5') or [],
            'ma20': series.get('ma20') or [],
            'ma60': series.get('ma60') or [],
        },
        'technical': health_payload.get('indicators') or {},
        'chip': {
            'institutional': health_payload.get('institutional') or {},
            'summary': rule_report.get('chips'),
            'chip_flow': war_room.get('chip_flow') or {},
        },
        'action_script': rule_report.get('action_scripts') or [],
        'diagnosis': {
            'summary': rule_report.get('summary'),
            'trend': rule_report.get('trend'),
            'chips': rule_report.get('chips'),
            'confidence': rule_report.get('confidence'),
        },
        'metadata': {
            'fallback_used': health_payload.get('fallback_used', False),
            'message': health_payload.get('message'),
            'price_map': war_room.get('price_map') or {},
        },
    }
    if payload['status'] == 'ok' and (not payload['quote'] or not payload['kline_ma']['candles']):
        payload['status'] = 'degraded'
        payload['warnings'] = [
            *payload['warnings'],
            'stock analysis payload is missing quote or k-line series',
        ]
    return payload


@app.route('/api/stock-analysis')
def api_stock_analysis():
    """Stable dashboard adapter for single-stock health analysis."""
    stock_id = str(request.args.get('id') or request.args.get('stock_id') or request.args.get('symbol') or '').strip()
    if not stock_id:
        return jsonify({'status': 'error', 'error': 'missing required stock id query parameter: id'}), 400

    try:
        health_payload = app_pkg._build_dashboard_health_check_payload(
            stock_id=stock_id,
            requested_date=request.args.get('date'),
            period=request.args.get('period'),
            overlays=_parse_csv_query_values('overlay', 'overlays'),
            panes=_parse_csv_query_values('pane', 'panes', 'panel', 'panels'),
        )
        status_code = 500 if health_payload.get('status') == 'error' else 200
        return _dashboard_json_response(_build_stock_analysis_payload(health_payload, stock_id), status_code)
    except Exception as exc:
        traceback.print_exc()
        return jsonify({'status': 'error', 'error': str(exc)}), 500


@app.route('/api/dashboard/macro')
def api_dashboard_macro():
    """回傳 dashboard beta 大盤總經 payload。"""
    try:
        payload = app_pkg._build_dashboard_macro_payload(request.args.get('date'))
        return _dashboard_json_response(payload)
    except Exception as exc:
        traceback.print_exc()
        return jsonify({'error': str(exc)}), 500


def _market_envelope(status: str, as_of_date: str | None, source: list[str], data: dict, warnings: list[str] | None = None):
    payload = {
        'status': status,
        'as_of_date': as_of_date,
        'source': source,
        'warnings': warnings or [],
        'data': data,
    }
    return _dashboard_json_response(payload)


def _load_market_frame() -> tuple[pd.DataFrame, str | None, list[str]]:
    warnings: list[str] = []
    requested_date = app_pkg.normalize_date_str(request.args.get('date')) or app_pkg._resolve_ui_baseline_date() or app_pkg._current_line_date()
    try:
        df, date_str = app_pkg.get_stock_data(date_str=requested_date)
    except Exception as exc:
        warnings.append(f'market data load failed: {exc}')
        return pd.DataFrame(), requested_date, warnings

    if not isinstance(df, pd.DataFrame):
        return pd.DataFrame(), app_pkg.normalize_date_str(date_str) or requested_date, ['market data returned no frame']

    resolved_date = app_pkg.normalize_date_str(date_str) or requested_date
    if not df.empty and 'trade_date' in df.columns:
        latest_date = app_pkg.normalize_date_str(df['trade_date'].max())
        if latest_date:
            resolved_date = latest_date
    return df.copy(), resolved_date, warnings


def _latest_market_rows(df: pd.DataFrame, as_of_date: str | None) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    frame = df.copy()
    if 'trade_date' in frame.columns and as_of_date:
        dated = frame[frame['trade_date'].astype(str).str[:10] == as_of_date]
        if not dated.empty:
            frame = dated
    if 'stock_id' in frame.columns and 'trade_date' in frame.columns:
        frame = frame.sort_values(['stock_id', 'trade_date']).groupby('stock_id', as_index=False).tail(1)
    return frame.copy()


def _record_from_row(row, fields: list[str]) -> dict[str, object]:
    record: dict[str, object] = {}
    for field in fields:
        value = row.get(field)
        if field in {'stock_id', 'stock_name', 'strategy', 'strategy_key', 'strategy_display', 'recommendation_date'}:
            record[field] = None if value is None or pd.isna(value) else str(value)
        elif field.endswith('_buy') or field in {'volume', 'margin_balance', 'short_balance'}:
            record[field] = app_pkg.safe_int(value)
        else:
            record[field] = app_pkg.safe_float(value)
    return record


def _recommendation_factor_fields(row) -> dict[str, object]:
    excluded = {
        'stock_id', 'stock_name', 'close_price', 'ai_score', 'strategy', 'strategy_key',
        'strategy_display', 'recommendation_date', 'fallback_used', 'chip_score',
    }
    factors: dict[str, object] = {}
    for field, value in row.items():
        key = str(field)
        normalized = key.lower()
        if normalized in excluded:
            continue
        if 'z_score' not in normalized and not normalized.endswith('_z'):
            continue
        if value is None or pd.isna(value):
            factors[key] = None
            continue
        numeric = app_pkg.safe_float(value)
        factors[key] = numeric if numeric is not None else str(value)
    return factors


def _top_records(frame: pd.DataFrame, column: str, *, ascending: bool = False, limit: int = 10, fields: list[str] | None = None) -> list[dict[str, object]]:
    if frame.empty or column not in frame.columns:
        return []
    fields = fields or ['stock_id', column]
    ranked = frame.copy()
    ranked[column] = pd.to_numeric(ranked[column], errors='coerce')
    ranked = ranked.dropna(subset=[column]).sort_values(column, ascending=ascending).head(limit)
    return [_record_from_row(row, fields) for _, row in ranked.iterrows()]


def _build_market_light(latest: pd.DataFrame) -> dict[str, object]:
    if latest.empty or 'close_price' not in latest.columns or 'open_price' not in latest.columns:
        return {'state': 'unavailable', 'label': 'Unavailable', 'score': None}
    close = pd.to_numeric(latest['close_price'], errors='coerce')
    open_price = pd.to_numeric(latest['open_price'], errors='coerce')
    up_count = int((close > open_price).sum())
    down_count = int((close < open_price).sum())
    total = max(up_count + down_count + int((close == open_price).sum()), 1)
    score = round(up_count / total * 100, 1)
    if score >= 55:
        state, label = 'bullish', '多方'
    elif score <= 45:
        state, label = 'bearish', '空方'
    else:
        state, label = 'neutral', '中性'
    return {'state': state, 'label': label, 'score': score}


def _build_institutional_totals(latest: pd.DataFrame) -> dict[str, int]:
    totals = {}
    for column in ('foreign_buy', 'trust_buy', 'dealer_buy'):
        if column in latest.columns:
            totals[column] = int(pd.to_numeric(latest[column], errors='coerce').fillna(0).sum())
        else:
            totals[column] = 0
    totals['total_net'] = totals['foreign_buy'] + totals['trust_buy'] + totals['dealer_buy']
    return totals


def _build_institutional_sync(totals: dict[str, int]) -> dict[str, object]:
    values = [totals.get('foreign_buy', 0), totals.get('trust_buy', 0), totals.get('dealer_buy', 0)]
    positive = sum(1 for value in values if value > 0)
    negative = sum(1 for value in values if value < 0)
    if positive >= 2:
        state = 'aligned_buy'
        label = '法人同步偏多'
    elif negative >= 2:
        state = 'aligned_sell'
        label = '法人同步偏空'
    else:
        state = 'mixed'
        label = '法人分歧'
    return {'state': state, 'label': label, 'net_total': totals.get('total_net', 0)}


@app.route('/api/market/summary')
def api_market_summary():
    df, as_of_date, warnings = _load_market_frame()
    latest = _latest_market_rows(df, as_of_date)
    totals = _build_institutional_totals(latest)
    heartbeat_payload, _ = _build_system_status_payload()
    status = 'ok' if not latest.empty and heartbeat_payload['status'] == 'ok' else 'degraded'
    return _market_envelope(
        status,
        as_of_date,
        ['daily_market_data', 'pipeline_runs'],
        {
            'market_light': _build_market_light(latest),
            'institutional_sync': _build_institutional_sync(totals),
            'system_heartbeat': heartbeat_payload['data']['heartbeat'],
        },
        warnings + heartbeat_payload['warnings'],
    )


@app.route('/api/market/recommendations')
def api_market_recommendations():
    try:
        top_n = max(1, min(int(request.args.get('top_n', 10)), 20))
    except (TypeError, ValueError):
        top_n = 10
    strategy = (request.args.get('strategy') or '').strip() or None
    as_of_date = app_pkg.normalize_date_str(request.args.get('date')) or app_pkg._resolve_ui_baseline_date() or app_pkg._current_line_date()
    warnings: list[str] = []
    try:
        rows = app_pkg.get_daily_recommendations(date_str=as_of_date, strategy=strategy, limit=top_n)
    except Exception as exc:
        warnings.append(f'recommendations load failed: {exc}')
        rows = pd.DataFrame()
    if not isinstance(rows, pd.DataFrame):
        rows = pd.DataFrame(rows or [])
    if rows.empty:
        return _market_envelope('empty', as_of_date, ['daily_recommendations'], {'recommendations': []}, warnings)
    fields = [
        'stock_id', 'stock_name', 'close_price', 'ai_score', 'strategy', 'strategy_key',
        'strategy_display', 'chip_score', 'rsi', 'volume', 'foreign_buy',
    ]
    ranked = rows.sort_values('ai_score', ascending=False).head(top_n) if 'ai_score' in rows.columns else rows.head(top_n)
    recommendations = []
    for _, row in ranked.iterrows():
        record = _record_from_row(row, [field for field in fields if field in ranked.columns])
        record.setdefault('recommendation_date', as_of_date)
        record['factor_fields'] = _recommendation_factor_fields(row)
        record['fallback_used'] = bool(row.get('fallback_used', False)) if 'fallback_used' in ranked.columns else False
        recommendations.append(record)
    return _market_envelope('ok', as_of_date, ['daily_recommendations'], {'recommendations': recommendations}, warnings)


@app.route('/api/market/snapshot')
def api_market_snapshot():
    df, as_of_date, warnings = _load_market_frame()
    latest = _latest_market_rows(df, as_of_date)
    if latest.empty:
        return _market_envelope('empty', as_of_date, ['daily_market_data'], {'breadth': {}, 'market_light': _build_market_light(latest)}, warnings)
    close = pd.to_numeric(latest.get('close_price'), errors='coerce')
    open_price = pd.to_numeric(latest.get('open_price'), errors='coerce')
    up_count = int((close > open_price).sum())
    down_count = int((close < open_price).sum())
    neutral_count = int((close == open_price).sum())
    total = max(up_count + down_count + neutral_count, 1)
    return _market_envelope(
        'ok',
        as_of_date,
        ['daily_market_data'],
        {
            'market_light': _build_market_light(latest),
            'breadth': {
                'up_count': up_count,
                'down_count': down_count,
                'neutral_count': neutral_count,
                'up_ratio': round(up_count / total * 100, 1),
                'down_ratio': round(down_count / total * 100, 1),
            },
        },
        warnings,
    )


@app.route('/api/market/institutional')
def api_market_institutional():
    df, as_of_date, warnings = _load_market_frame()
    latest = _latest_market_rows(df, as_of_date)
    if latest.empty:
        return _market_envelope('empty', as_of_date, ['daily_market_data'], {'totals': _build_institutional_totals(latest)}, warnings)
    data = {'totals': _build_institutional_totals(latest), 'top': {}, 'synchronized_buys': []}
    for column in ('foreign_buy', 'trust_buy', 'dealer_buy'):
        data['top'][column] = {
            'buy': _top_records(latest, column, ascending=False, fields=['stock_id', column]),
            'sell': _top_records(latest, column, ascending=True, fields=['stock_id', column]),
        }
    available_flow = [column for column in ('foreign_buy', 'trust_buy', 'dealer_buy') if column in latest.columns]
    if available_flow:
        sync = latest.copy()
        for column in available_flow:
            sync[column] = pd.to_numeric(sync[column], errors='coerce').fillna(0)
        sync['sync_count'] = sync[available_flow].gt(0).sum(axis=1)
        sync['total_net'] = sync[available_flow].sum(axis=1)
        sync = sync[(sync['sync_count'] >= 2) & (sync['total_net'] > 0)].sort_values('total_net', ascending=False).head(10)
        data['synchronized_buys'] = [_record_from_row(row, ['stock_id', 'foreign_buy', 'trust_buy', 'dealer_buy', 'total_net']) for _, row in sync.iterrows()]
    return _market_envelope('ok' if len(available_flow) == 3 else 'degraded', as_of_date, ['daily_market_data'], data, warnings)


@app.route('/api/market/technical')
def api_market_technical():
    df, as_of_date, warnings = _load_market_frame()
    latest = _latest_market_rows(df, as_of_date)
    if latest.empty:
        return _market_envelope('empty', as_of_date, ['daily_market_data'], {}, warnings)
    weekly = latest.copy()
    if 'stock_id' in df.columns and 'trade_date' in df.columns and 'close_price' in df.columns:
        history = df.copy()
        history['close_price'] = pd.to_numeric(history['close_price'], errors='coerce')
        previous = history.sort_values(['stock_id', 'trade_date']).groupby('stock_id', as_index=False).first()[['stock_id', 'close_price']]
        previous = previous.rename(columns={'close_price': 'previous_close'})
        weekly = latest.merge(previous, on='stock_id', how='left')
        weekly['weekly_gain_pct'] = ((pd.to_numeric(weekly['close_price'], errors='coerce') - weekly['previous_close']) / weekly['previous_close'] * 100).replace([float('inf'), -float('inf')], pd.NA)
    else:
        weekly['weekly_gain_pct'] = pd.NA
    breakout = latest.copy()
    breakout['volume'] = pd.to_numeric(breakout.get('volume'), errors='coerce')
    breakout['close_price'] = pd.to_numeric(breakout.get('close_price'), errors='coerce')
    breakout['open_price'] = pd.to_numeric(breakout.get('open_price'), errors='coerce')
    breakout = breakout[(breakout['close_price'] > breakout['open_price']) & (breakout['volume'] >= breakout['volume'].median())]
    rsi_frame = latest.copy()
    rsi_frame['rsi'] = pd.to_numeric(rsi_frame.get('rsi'), errors='coerce')
    data = {
        'weekly_gain_top10': _top_records(weekly, 'weekly_gain_pct', fields=['stock_id', 'weekly_gain_pct', 'close_price']),
        'volume_price_breakouts': [_record_from_row(row, ['stock_id', 'close_price', 'volume']) for _, row in breakout.sort_values('volume', ascending=False).head(10).iterrows()],
        'rsi_overbought': [_record_from_row(row, ['stock_id', 'rsi', 'close_price']) for _, row in rsi_frame[rsi_frame['rsi'] >= 70].sort_values('rsi', ascending=False).head(10).iterrows()],
        'rsi_oversold': [_record_from_row(row, ['stock_id', 'rsi', 'close_price']) for _, row in rsi_frame[rsi_frame['rsi'] <= 30].sort_values('rsi', ascending=True).head(10).iterrows()],
    }
    return _market_envelope('ok', as_of_date, ['daily_market_data'], data, warnings)


@app.route('/api/market/margin')
def api_market_margin():
    df, as_of_date, warnings = _load_market_frame()
    latest = _latest_market_rows(df, as_of_date)
    if latest.empty or not {'margin_balance', 'short_balance'}.intersection(latest.columns):
        return _market_envelope('empty', as_of_date, ['daily_market_data'], {}, warnings)
    margin = latest.copy()
    if 'stock_id' in df.columns and 'trade_date' in df.columns:
        history = df.sort_values(['stock_id', 'trade_date']).copy()
        prev = history.groupby('stock_id', as_index=False).first()[['stock_id'] + [c for c in ['margin_balance', 'short_balance'] if c in history.columns]]
        prev = prev.rename(columns={'margin_balance': 'previous_margin_balance', 'short_balance': 'previous_short_balance'})
        margin = margin.merge(prev, on='stock_id', how='left')
    for column in ('margin_balance', 'short_balance', 'previous_margin_balance', 'previous_short_balance'):
        if column in margin.columns:
            margin[column] = pd.to_numeric(margin[column], errors='coerce')
    margin['margin_change'] = margin.get('margin_balance', 0) - margin.get('previous_margin_balance', 0)
    margin['short_change'] = margin.get('short_balance', 0) - margin.get('previous_short_balance', 0)
    margin['short_margin_ratio'] = (margin.get('short_balance', 0) / margin.get('margin_balance', 1)).replace([float('inf'), -float('inf')], pd.NA)
    data = {
        'margin_increase': _top_records(margin, 'margin_change', fields=['stock_id', 'margin_balance', 'margin_change']),
        'margin_decrease': _top_records(margin, 'margin_change', ascending=True, fields=['stock_id', 'margin_balance', 'margin_change']),
        'short_increase': _top_records(margin, 'short_change', fields=['stock_id', 'short_balance', 'short_change']),
        'short_decrease': _top_records(margin, 'short_change', ascending=True, fields=['stock_id', 'short_balance', 'short_change']),
        'high_short_margin_risk': _top_records(margin, 'short_margin_ratio', fields=['stock_id', 'short_balance', 'margin_balance', 'short_margin_ratio']),
    }
    return _market_envelope('ok', as_of_date, ['daily_market_data'], data, warnings)


def _format_pipeline_status(status: str | None) -> str:
    normalized = str(status or '').strip().lower()
    return {
        'success': 'Success',
        'failed': 'Failed',
        'failure': 'Failed',
        'running': 'Running',
        'not_run': 'Not Run',
        'not run': 'Not Run',
    }.get(normalized, 'Unknown')


def _build_system_status_payload() -> tuple[dict[str, object], int]:
    step_map = [('1_update', 'update_database'), ('2_run', 'run_daily'), ('5_push', 'push_to_line')]
    warnings: list[str] = []
    rows_by_step: dict[str, dict[str, object]] = {}
    try:
        engine = db_helper.get_db_engine()
        db_helper.ensure_pipeline_run_state_schema(engine)
        with engine.connect() as conn:
            for _, step_name in step_map:
                row = conn.execute(
                    text(
                        f"""
                        SELECT *
                        FROM {db_helper.PIPELINE_RUNS_TABLE}
                        WHERE step_name = :step_name
                        ORDER BY run_date DESC, updated_at DESC, id DESC
                        LIMIT 1
                        """
                    ),
                    {'step_name': step_name},
                ).mappings().fetchone()
                if row:
                    rows_by_step[step_name] = dict(row)
    except Exception as exc:
        warnings.append(f'pipeline status load failed: {exc}')
    steps = []
    for alias, step_name in step_map:
        row = rows_by_step.get(step_name) or {}
        steps.append(
            {
                'alias': alias,
                'step_name': step_name,
                'status': _format_pipeline_status(row.get('status')),
                'run_date': app_pkg.normalize_date_str(row.get('run_date')),
                'trade_date': app_pkg.normalize_date_str(row.get('trade_date')),
                'started_at': str(row.get('started_at')) if row.get('started_at') is not None else None,
                'finished_at': str(row.get('finished_at')) if row.get('finished_at') is not None else None,
                'rows_inserted': app_pkg.safe_int(row.get('rows_inserted')),
                'rows_updated': app_pkg.safe_int(row.get('rows_updated')),
                'error_summary': row.get('error_summary'),
            }
        )
    has_failed = any(step['status'] == 'Failed' for step in steps)
    has_missing = any(step['status'] == 'Unknown' for step in steps)
    status = 'degraded' if has_failed or has_missing or warnings else 'ok'
    latest_run_date = next((step['run_date'] for step in steps if step.get('run_date')), None)
    heartbeat_state = 'failed' if has_failed else ('not_run' if has_missing else 'success')
    payload = {
        'status': status,
        'as_of_date': latest_run_date or app_pkg._current_line_date(),
        'source': ['pipeline_runs'],
        'warnings': warnings,
        'data': {
            'heartbeat': {'state': heartbeat_state, 'latest_run_date': latest_run_date},
            'steps': steps,
        },
    }
    return payload, 200


@app.route('/api/market/system-status')
def api_market_system_status():
    payload, status_code = _build_system_status_payload()
    return _dashboard_json_response(payload, status_code)


@app.route('/backtest', methods=['GET', 'POST'])
@login_required
def backtest():
    """回測頁面。"""
    if request.method == 'GET':
        available_strategies = list(app_pkg.strategy_manager.STRATEGY_REGISTRY.keys())
        return render_template('backtest.html', strategies=available_strategies)

    try:
        from core.viz_helper import generate_report_from_csv

        selected_strategies = request.form.getlist('strategies')
        start_date = request.form.get('start_date')
        end_date = request.form.get('end_date')
        weights_raw = (request.form.get('weights') or '').strip()
        weights = [float(w.strip()) for w in weights_raw.split(',') if w.strip()] if weights_raw else None

        if not selected_strategies:
            flash('請至少選擇一個策略', 'error')
            return redirect(url_for('backtest'))

        result, start_date, end_date = app_pkg._run_portfolio_backtest(
            selected_strategies,
            start_date=start_date,
            end_date=end_date,
            weights=weights,
        )
        report = generate_report_from_csv()
        return render_template(
            'backtest_result.html',
            metrics=result['metrics'],
            strategy_performance=result['strategy_performance'],
            equity_chart=report['equity_curve'],
            drawdown_chart=report['drawdown'],
            monthly_chart=report['monthly_returns'],
            selected_strategies=selected_strategies,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as exc:
        traceback.print_exc()
        flash(f'回測執行失敗: {str(exc)}', 'error')
        return redirect(url_for('backtest'))


@app.route('/api/backtest/run', methods=['POST'])
@login_required
def api_run_backtest():
    """執行回測 API。"""
    try:
        data = request.get_json() or {}
        selected_strategies = data.get('strategies', ['v31_hybrid'])
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        raw_weights = data.get('weights')

        weights = None
        if isinstance(raw_weights, list):
            weights = [float(w) for w in raw_weights]
        elif isinstance(raw_weights, str) and raw_weights.strip():
            weights = [float(w.strip()) for w in raw_weights.split(',') if w.strip()]

        result, _, _ = app_pkg._run_portfolio_backtest(
            selected_strategies,
            start_date=start_date,
            end_date=end_date,
            weights=weights,
        )
        return jsonify(
            {
                'success': True,
                'data': {
                    'metrics': result['metrics'],
                    'strategy_performance': result['strategy_performance'],
                },
            }
        )
    except Exception as exc:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(exc)}), 500
