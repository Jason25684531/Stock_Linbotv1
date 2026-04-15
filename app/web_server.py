"""Canonical Flask web entrypoint and dashboard routes."""

from __future__ import annotations

import os
import sys
import traceback

import pandas as pd
from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from config import Config, V34_MODE_PRESETS, V35_MODE_PRESETS
from . import app

app_pkg = sys.modules[__package__]


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

        df, date_str = app_pkg.get_stock_data()
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
                requested_date=date_str,
                limit=top_n,
            )

            if not has_persisted and not candidates.empty:
                try:
                    from core.model_utils import load_model as _load_model

                    strat_model, strat_features, _, _ = _load_model(strategy_key)
                    if strat_model is not None:
                        features = strat_features or active.features
                        score_df = candidates.copy()
                        for feature_name in features:
                            if feature_name not in score_df.columns:
                                score_df[feature_name] = 0
                        probs = strat_model.predict_proba(score_df[features].fillna(0))[:, 1]
                        candidates = candidates.copy()
                        candidates['ai_score'] = probs
                        candidates = candidates.sort_values('ai_score', ascending=False)
                except Exception as score_error:
                    print(f'⚠️ /api/daily-signals AI 排名失敗: {score_error}')

            if not has_persisted and not candidates.empty and 'ai_score' in candidates.columns:
                candidates = app_pkg._apply_news_sentiment_overlay(
                    candidates,
                    fallback_meta.get('recommendation_date') or date_str,
                )

            if candidates.empty:
                return jsonify(
                    {
                        'date': fallback_meta.get('recommendation_date') or date_str,
                        'requested_date': fallback_meta.get('requested_date') or date_str,
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
        stock_mentions_map = app_pkg._get_stock_mentions_map([str(sid) for sid in picks['stock_id'].tolist()])
        for _, row in picks.iterrows():
            close_price = float(row['close_price'])
            stop_loss_rate = float(getattr(active_strategy, 'stop_loss', Config.V30_STOP_LOSS)) if active_strategy else Config.V30_STOP_LOSS
            take_profit_rate = float(getattr(active_strategy, 'take_profit', Config.V30_TAKE_PROFIT)) if active_strategy else Config.V30_TAKE_PROFIT
            news_info = app_pkg._resolve_signal_news_info(row, signal_date, stock_mentions_map)

            signal = {
                'stock_id': row['stock_id'],
                'close_price': close_price,
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