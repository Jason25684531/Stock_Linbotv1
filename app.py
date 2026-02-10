"""
Line Bot 主程式 (V31 混合策略版)
============================================
功能:
1. V31 混合策略選股（V30篩選 + ML智慧排名）
2. V30 純技術分析選股（均線突破+量能確認）
3. 個股查詢（含策略報告+停損停利）
4. 動態參數調整（資料庫設定）
5. 目標：獲利 10-20%，停損 5%
"""
# -*- coding: utf-8 -*-
import sys
import io

# 修復 Windows 終端機 UTF-8 編碼問題
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import pandas as pd
from sqlalchemy import create_engine, text
import joblib
import os
from flask import Flask, request, abort, render_template, jsonify, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user

# Line Bot SDK v3 (2024更新)
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage as V3TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from config import Config

# 引入策略模組
from tool.strategy import (
    calculate_pivot_strategy, format_strategy_message, calculate_position_size, 
    calculate_v30_signal, get_best_stocks_v31_hybrid, get_v30_params_from_db,
    format_v30_recommendation, format_v31_recommendation, format_stock_query
)
# 引入資料庫輔助模組
from tool.db_helper import get_setting, update_setting, validate_setting, get_stock_data
# 引入策略工廠
from tool.strategy_manager import StrategyManager

app = Flask(__name__)
app.secret_key = Config.FLASK_SECRET_KEY

# ==========================================
# 🔐 Flask-Login 設定 (Phase 1 Security)
# ==========================================
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = '請先登入以存取此頁面'
login_manager.login_message_category = 'error'


class User(UserMixin):
    """簡易使用者類別 (基於環境變數驗證)
    
    只有一個管理員帳號，密碼從環境變數讀取
    """
    def __init__(self, user_id):
        self.id = user_id
    
    @staticmethod
    def validate_password(password):
        """驗證密碼是否正確"""
        return password == Config.ADMIN_PASSWORD
    
    @staticmethod
    def get(user_id):
        """取得使用者物件"""
        if user_id == 'admin':
            return User('admin')
        return None


@login_manager.user_loader
def load_user(user_id):
    """Flask-Login 回呼：載入使用者"""
    return User.get(user_id)

# Line Bot SDK v3 設定
configuration = Configuration(access_token=Config.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(Config.LINE_CHANNEL_SECRET)

# 載入模型
print("[AI] 正在載入 AI 模型...")
model = None
try:
    if os.path.exists(Config.MODEL_PATH):
        model = joblib.load(Config.MODEL_PATH)
    elif os.path.exists('stock_ai_model.pkl'):
        model = joblib.load('stock_ai_model.pkl')
    print("[OK] 模型載入成功")
except Exception as e:
    print(f"[WARNING] 模型載入失敗: {e}")

# 初始化策略管理器
print("[AI] 正在初始化策略管理器...")
strategy_manager = StrategyManager()
print(f"[OK] 當前策略: {strategy_manager.get_active_strategy_name()}")


# ============================================
# 🔧 設定管理函數已移至 tool.db_helper 模組
# ============================================



# ============================================
# 📊 核心業務邏輯
# ============================================


def get_v30_recommendation():
    """
    V30 策略選股（均線突破 + 量能確認）
    已在回測中實現 40% 報酬率
    
    Returns:
        推薦訊息字串
    """
    try:
        # 撈取最新資料
        df, date_str = get_stock_data()
        if df.empty: 
            return "💤 今日無資料"

        # 確保必要欄位存在
        required_cols = ['close_price', 'ma20', 'ma60', 'volume', 'rsi']
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            return f"⚠️ 資料庫缺少欄位: {', '.join(missing_cols)}\n請執行 tool/calc_indicators.py"

        # 套用 V30 策略篩選
        picks = []
        for _, row in df.iterrows():
            v30_result = calculate_v30_signal(row)
            if v30_result['signal_strength'] == 'strong':
                picks.append({
                    'stock_id': row['stock_id'],
                    'close_price': row['close_price'],
                    'rsi': row.get('rsi', 0),
                    'volume': row.get('volume', 0),
                    'stop_loss': v30_result['stop_loss'],
                    'take_profit': v30_result['take_profit'],
                    'foreign_buy': row.get('foreign_buy', 0),
                })

        # 使用 Strategy 模組的格式化函數
        return format_v30_recommendation(picks, date_str)
        
    except Exception as e:
        import traceback
        print(f"❌ V30 推薦失敗: {e}")
        traceback.print_exc()
        return f"❌ 運算錯誤: {str(e)[:100]}"


def get_ai_recommendation():
    """
    V31 混合策略選股（V30 篩選 + ML 智慧排名）
    
    Returns:
        推薦訊息字串
    """
    try:
        # 1. 撈取最新資料
        df, date_str = get_stock_data()
        if df.empty: 
            return "💤 今日無資料"

        # 2. 使用 V31 混合策略選股
        picks = get_best_stocks_v31_hybrid(df, top_n=5)
        
        # 3. 使用 Strategy 模組的格式化函數
        return format_v31_recommendation(picks, date_str)
        
    except Exception as e:
        import traceback
        print(f"❌ V31 推薦失敗: {e}")
        traceback.print_exc()
        return f"❌ 運算錯誤: {str(e)[:100]}"


def query_stock(stock_id):
    """
    個股查詢（V2.0 完整策略報告版）
    
    Args:
        stock_id: 股票代號
    
    Returns:
        策略報告字串
    """
    try:
        # 1. 撈取資料
        df, date_str = get_stock_data(stock_id=stock_id)
        if df.empty: 
            return f"🔍 找不到 {stock_id} 的資料"
        
        row = df.iloc[0]
        
        # 2. AI 預測
        if model:
            df_feat = pd.DataFrame([row])
            for f in Config.FEATURES: 
                if f not in df_feat.columns: 
                    df_feat[f] = 0
            prob = model.predict_proba(df_feat[Config.FEATURES].fillna(0))[:, 1][0]
        else:
            prob = 0.5
        
        # 3. 判斷是否啟用完整策略報告
        enable_strategy = get_setting('enable_strategy_report', 'true') == 'true'
        
        # 4. 使用 Strategy 模組的格式化函數
        return format_stock_query(stock_id, date_str, row, prob, enable_strategy)
        
    except Exception as e:
        import traceback
        print(f"❌ 個股查詢失敗: {e}")
        traceback.print_exc()
        return f"❌ 查詢失敗: {str(e)[:100]}"


def get_settings_info():
    """
    查看當前設定
    
    Returns:
        設定資訊字串
    """
    try:
        # AI 設定
        ai_threshold = float(get_setting('ai_threshold', '0.5'))
        
        # V30 策略參數
        v30_stop_loss = float(get_setting('v30_stop_loss', str(Config.V30_PARAMS['STOP_LOSS'])))
        v30_take_profit = float(get_setting('v30_take_profit', str(Config.V30_PARAMS['TAKE_PROFIT'])))
        v30_max_hold = int(get_setting('v30_max_hold_days', str(Config.V30_PARAMS['MAX_HOLD_DAYS'])))
        
        msg = "⚙️ 【當前設定】\n"
        msg += "-" * 30 + "\n"
        msg += "🚀 V30 策略參數:\n"
        msg += f"  🛡️ 停損: {int(v30_stop_loss*100)}%\n"
        if v30_take_profit > 0:
            msg += f"  🎯 停利: {int(v30_take_profit*100)}%\n"
        else:
            msg += f"  🎯 停利: 不停利（持有至到期）\n"
        msg += f"  ⏰ 最長持有: {v30_max_hold}天\n"
        msg += "\n"
        msg += "🧠 AI 參數:\n"
        msg += f"  AI 門檻: {int(ai_threshold*100)}%\n"
        msg += "-" * 30 + "\n"
        msg += "💡 可用指令:\n"
        msg += "• 設定停損 5 (設為5%)\n"
        msg += "• 設定停利 20 (設為20%)\n"
        msg += "• 設定停利 0 (不停利)\n"
        msg += "• 設定信心 60 (AI門檻60%)"
        
        return msg
    except Exception as e:
        return f"❌ 讀取設定失敗: {e}"



# ============================================
# 🌐 Flask 路由
# ============================================

# ==========================================
# 🔐 登入/登出路由 (Phase 1 Security)
# ==========================================

@app.route("/login", methods=['GET', 'POST'])
def login():
    """登入頁面"""
    # 如果已登入，重定向到 Dashboard
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        password = request.form.get('password', '')
        
        if User.validate_password(password):
            user = User('admin')
            login_user(user)
            flash('✅ 登入成功！', 'success')
            
            # 重定向到原本要訪問的頁面，或 Dashboard
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard'))
        else:
            flash('❌ 密碼錯誤，請重試', 'error')
    
    return render_template('login.html')


@app.route("/logout")
@login_required
def logout():
    """登出"""
    logout_user()
    flash('👋 已登出', 'success')
    return redirect(url_for('login'))


# ==========================================
# V32: Web Dashboard 路由
# ==========================================

@app.route("/")
@login_required
def index():
    """首頁重定向到 Dashboard"""
    return redirect(url_for('dashboard'))


@app.route("/dashboard")
@login_required
def dashboard():
    """V32 Dashboard 主頁面"""
    # 傳遞策略資訊給前端
    active_strategies = strategy_manager.get_active_strategy_names()
    strategy_options = strategy_manager.list_strategies()
    
    return render_template('dashboard.html', 
                         active_strategies=active_strategies,
                         strategy_options=strategy_options,
                         current_strategy=active_strategies[0] if active_strategies else 'v31_hybrid')


@app.route('/update_strategy', methods=['POST'])
@login_required
def update_strategy():
    """切換策略 (V2: 支援多策略)"""
    try:
        # 使用 getlist 取得多個 checkbox 的值
        selected_strategies = request.form.getlist('strategies')
        
        if not selected_strategies:
            flash('請至少選擇一個策略', 'error')
            return redirect(url_for('dashboard'))
        
        # 設定多策略
        success = strategy_manager.set_active_strategies(selected_strategies)
        
        if success:
            if len(selected_strategies) == 1:
                strategy_obj = strategy_manager.get_active_strategy()
                flash(f'✅ 已切換至 {strategy_obj.display_name}', 'success')
            else:
                flash(f'✅ 已啟用 {len(selected_strategies)} 個策略', 'success')
            print(f"[Strategy] 切換至: {selected_strategies}")
        else:
            flash('❌ 策略切換失敗', 'error')
        
    except Exception as e:
        flash(f'❌ 切換失敗: {str(e)}', 'error')
        print(f"[ERROR] 策略切換失敗: {e}")
    
    return redirect(url_for('dashboard'))


@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route("/api/performance")
def api_performance():
    """
    API: 取得回測資產曲線數據
    Returns: JSON {dates: [], equity: [], roi: []}
    """
    try:
        profit_file = 'ML_Data/backtest_profit_report.csv'
        if not os.path.exists(profit_file):
            return jsonify({'error': '回測數據不存在，請先執行 4_run_backtest.py'}), 404
        
        df = pd.read_csv(profit_file)
        
        return jsonify({
            'dates': df['date'].tolist(),
            'equity': df['asset_value'].tolist(),
            'roi': df['roi'].tolist()
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route("/api/trades")
def api_trades():
    """
    API: 取得交易明細（最近 50 筆）
    Returns: JSON list of trades
    """
    try:
        trades_file = 'ML_Data/backtest_result.csv'
        if not os.path.exists(trades_file):
            return jsonify({'error': '交易數據不存在，請先執行 4_run_backtest.py'}), 404
        
        df = pd.read_csv(trades_file)
        
        # 只回傳最近 50 筆
        df_recent = df.tail(50)
        
        # 轉換為 JSON
        trades = df_recent.to_dict('records')
        
        return jsonify(trades)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route("/api/summary")
def api_summary():
    """
    API: 取得回測摘要統計
    Returns: JSON {total_roi, max_drawdown, sharpe_ratio, win_rate}
    """
    try:
        from tool.viz_helper import get_backtest_summary
        summary = get_backtest_summary()
        if summary is None:
            return jsonify({'error': '回測數據不存在'}), 404
        return jsonify(summary)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==========================================
# 🎮 V33 Phase 3: PK System API
# ==========================================
@app.route("/api/user/trade", methods=['POST'])
def api_user_trade():
    """
    API: 記錄使用者模擬交易
    Request Body: {user_id, stock_id, buy_price, buy_date}
    """
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        stock_id = data.get('stock_id')
        buy_price = data.get('buy_price')
        buy_date = data.get('buy_date')
        
        if not all([user_id, stock_id, buy_price, buy_date]):
            return jsonify({'error': '缺少必要參數'}), 400
        
        # 插入資料庫
        from tool.db_helper import get_db_engine
        engine = get_db_engine()
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO user_simulation_trades 
                (user_id, stock_id, buy_price, buy_date, status)
                VALUES (:user_id, :stock_id, :buy_price, :buy_date, 'HOLDING')
            """), {
                'user_id': user_id,
                'stock_id': stock_id,
                'buy_price': float(buy_price),
                'buy_date': buy_date
            })
            conn.commit()
        
        return jsonify({'success': True, 'message': '模擬交易記錄成功'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route("/api/pk/battle", methods=['GET'])
def api_pk_battle():
    """
    API: 取得人機對決統計數據
    Returns: JSON {user_roi, ai_roi, user_win_rate, ai_win_rate}
    """
    try:
        # Mock 數據示範（未來可連接真實交易記錄）
        user_roi = 15.5  # 使用者報酬率
        ai_roi = 19.2    # AI 報酬率 (來自 backtest_result.csv)
        
        # 從 backtest_result.csv 讀取 AI 實際數據
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
        
        # Mock 使用者數據（未來從 user_simulation_trades 計算）
        user_win_rate = 45
        
        return jsonify({
            'user_roi': round(user_roi, 2),
            'ai_roi': round(ai_roi, 2),
            'user_win_rate': round(user_win_rate, 2),
            'ai_win_rate': round(ai_win_rate, 2)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route("/api/live_signals")
def api_live_signals():
    """
    API: 取得回測總結數據
    Returns: JSON {total_roi, win_rate, mdd, sharpe, trade_count, avg_hold_days}
    """
    try:
        from tool.viz_helper import get_backtest_summary
        summary = get_backtest_summary()
        if summary is None:
            return jsonify({'error': '數據不存在'}), 404
        
        # 重新映射鍵名以符合前端期望
        return jsonify({
            'total_roi': summary['total_roi'],
            'win_rate': summary['win_rate'],
            'mdd': summary['max_drawdown'],
            'sharpe': summary['sharpe_ratio'],
            'trade_count': summary['trade_count'],
            'avg_hold_days': summary['avg_hold_days']
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route("/api/daily-signals")
def api_daily_signals():
    """
    V32 Phase 4: 取得今日選股訊號
    Returns: JSON list of recommended stocks for today
    """
    try:
        # 取得最新資料
        df, date_str = get_stock_data()
        
        if df.empty:
            return jsonify({
                'error': '無最新資料',
                'date': None,
                'signals': []
            })
        
        # 使用 V31 混合策略選股
        picks = get_best_stocks_v31_hybrid(df, top_n=5)
        
        if picks.empty:
            return jsonify({
                'date': date_str,
                'signals': [],
                'message': '今日無符合條件的股票'
            })
        
        # 格式化輸出
        signals = []
        for _, row in picks.iterrows():
            signal = {
                'stock_id': row['stock_id'],
                'close_price': float(row['close_price']),
                'strategy': 'V31 混合策略',
                'ai_score': float(row.get('ai_score', 0)) if 'ai_score' in row else None,
                'rsi': float(row.get('rsi', 0)) if 'rsi' in row else None,
                'volume': int(row.get('volume', 0)) if 'volume' in row else None,
                'ma20': float(row.get('ma20', 0)) if 'ma20' in row else None,
                'foreign_buy': int(row.get('foreign_buy', 0)) if 'foreign_buy' in row else None
            }
            signals.append(signal)
        
        return jsonify({
            'date': date_str,
            'signals': signals,
            'count': len(signals)
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ==========================================
# Phase 5: Backtesting & Visualization
# ==========================================

@app.route("/backtest", methods=['GET', 'POST'])
@login_required
def backtest():
    """
    回測頁面
    - GET: 顯示回測設定表單
    - POST: 執行回測並顯示結果
    """
    if request.method == 'GET':
        # 顯示回測設定頁面
        available_strategies = list(strategy_manager.STRATEGY_REGISTRY.keys())
        return render_template('backtest.html', strategies=available_strategies)
    
    # POST: 執行回測
    try:
        from tool.viz_helper import generate_report_from_csv
        from datetime import datetime, timedelta
        import sys
        import os
        
        # 取得表單資料
        selected_strategies = request.form.getlist('strategies')  # 多選策略
        start_date = request.form.get('start_date')
        end_date = request.form.get('end_date')
        
        if not selected_strategies:
            flash('請至少選擇一個策略', 'error')
            return redirect(url_for('backtest'))
        
        # 設定預設日期（最近 1 年）
        if not start_date:
            start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        if not end_date:
            end_date = datetime.now().strftime('%Y-%m-%d')
        
        # 執行組合回測
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from importlib import import_module
        backtest_module = import_module('4_run_backtest')
        
        engine = backtest_module.PortfolioBacktestEngine(
            strategies=selected_strategies,
            start_date=start_date,
            end_date=end_date
        )
        
        result = engine.run_portfolio_backtest()
        
        # 生成視覺化報告
        report = generate_report_from_csv()
        
        # 傳遞給模板
        return render_template(
            'backtest_result.html',
            metrics=result['metrics'],
            strategy_performance=result['strategy_performance'],
            equity_chart=report['equity_curve'],
            drawdown_chart=report['drawdown'],
            monthly_chart=report['monthly_returns'],
            selected_strategies=selected_strategies,
            start_date=start_date,
            end_date=end_date
        )
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        flash(f'回測執行失敗: {str(e)}', 'error')
        return redirect(url_for('backtest'))


@app.route("/api/backtest/run", methods=['POST'])
@login_required
def api_run_backtest():
    """
    API: 執行回測
    Returns: JSON {success, data}
    """
    try:
        from datetime import datetime, timedelta
        import sys
        import os
        
        data = request.get_json()
        selected_strategies = data.get('strategies', ['v31_hybrid'])
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        
        # 設定預設日期
        if not start_date:
            start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        if not end_date:
            end_date = datetime.now().strftime('%Y-%m-%d')
        
        # 執行回測
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from importlib import import_module
        backtest_module = import_module('4_run_backtest')
        
        engine = backtest_module.PortfolioBacktestEngine(
            strategies=selected_strategies,
            start_date=start_date,
            end_date=end_date
        )
        
        result = engine.run_portfolio_backtest()
        
        return jsonify({
            'success': True,
            'data': {
                'metrics': result['metrics'],
                'strategy_performance': result['strategy_performance']
            }
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==========================================
# Line Bot Webhook 路由
# ==========================================

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    """
    Line 訊息處理中心（V2.0 完整指令版）
    """
    msg_text = event.message.text.strip()
    
    # ========== 設定管理指令 ==========
    if msg_text == "切換積極":
        if update_setting('mode', 'aggressive'):
            reply = "😈 已切換至【積極模式】\n放寬篩選條件，提高選股數量"
        else:
            reply = "❌ 切換失敗，請稍後再試"
            
    elif msg_text == "切換穩健":
        if update_setting('mode', 'conservative'):
            reply = "🛡️ 已切換至【穩健模式】\n嚴格篩選，只選站上月線股票"
        else:
            reply = "❌ 切換失敗，請稍後再試"
            
    elif msg_text.startswith("設定信心"):
        try:
            # 解析數字：「設定信心 60」→ 60
            value_str = msg_text.replace("設定信心", "").strip()
            value = float(value_str) / 100
            
            # 驗證範圍
            is_valid, err_msg = validate_setting('ai_threshold', str(value))
            if not is_valid:
                reply = f"❌ {err_msg}\n範例：設定信心 60（代表60%）"
            elif update_setting('ai_threshold', str(value)):
                reply = f"🧠 AI 信心門檻已設為 {int(value*100)}%\n將只推薦高於此門檻的股票"
            else:
                reply = "❌ 設定失敗"
        except ValueError:
            reply = "❌ 格式錯誤\n正確用法：設定信心 60"
    
    # ========== V30 參數調整指令 ==========
    elif msg_text.startswith("設定停損"):
        try:
            value_str = msg_text.replace("設定停損", "").strip()
            value = float(value_str) / 100
            if 0.01 <= value <= 0.20:  # 1%-20% 範圍
                if update_setting('v30_stop_loss', str(value)):
                    reply = f"🛡️ V30停損已設為 {int(value*100)}%\n下次選股將使用新參數"
                else:
                    reply = "❌ 設定失敗"
            else:
                reply = "❌ 停損需在 1%-20% 之間\n範例：設定停損 5"
        except ValueError:
            reply = "❌ 格式錯誤\n正確用法：設定停損 5（代表5%）"
    
    elif msg_text.startswith("設定停利"):
        try:
            value_str = msg_text.replace("設定停利", "").strip()
            if value_str == "0" or value_str.lower() == "不停利":
                if update_setting('v30_take_profit', '0'):
                    params = get_v30_params_from_db()
                    reply = f"🎯 V30停利已取消\n將持有至停損或到期（{params['MAX_HOLD_DAYS']}天）"
                else:
                    reply = "❌ 設定失敗"
            else:
                value = float(value_str) / 100
                if 0.05 <= value <= 0.50:  # 5%-50% 範圍
                    if update_setting('v30_take_profit', str(value)):
                        reply = f"🎯 V30停利已設為 {int(value*100)}%\n下次選股將使用新參數"
                    else:
                        reply = "❌ 設定失敗"
                else:
                    reply = "❌ 停利需在 5%-50% 之間\n範例：設定停利 20（代表20%）\n或輸入「設定停利 0」取消停利"
        except ValueError:
            reply = "❌ 格式錯誤\n用法：\n• 設定停利 20（20%停利）\n• 設定停利 0（不停利）"
            
    elif msg_text == "查看設定":
        reply = get_settings_info()
        
    # ========== 核心功能指令 ==========
    elif msg_text in ["V30", "v30", "策略"]:
        # V30 策略選股（40% 報酬實績）
        reply = get_v30_recommendation()
        
    elif msg_text in ["推薦", "選股", "AI"]:
        reply = get_ai_recommendation()
        
    elif msg_text.isdigit() and len(msg_text) == 4:  # 股票代號（4碼）
        reply = query_stock(msg_text)
        
    elif msg_text.startswith("查詢"):
        stock_id = msg_text.replace("查詢", "").strip()
        if stock_id.isdigit():
            reply = query_stock(stock_id)
        else:
            reply = "❌ 請輸入正確的股票代號"
    
    # ========== V32: Dashboard 連結 ==========
    elif msg_text in ["dashboard", "儀表板", "Dashboard", "看板"]:
        reply = "📊 【V32 量化交易儀表板】\n\n"
        reply += "🔗 Dashboard URL:\n"
        reply += "http://localhost:5000/dashboard\n\n"
        reply += "📈 功能:\n"
        reply += "• 資產曲線圖 (Equity Curve)\n"
        reply += "• 回測績效指標 (ROI, MDD, Sharpe)\n"
        reply += "• 交易明細表 (Recent Trades)\n"
        reply += "• 即時選股訊號 (Live Signals)\n\n"
        reply += "💡 提示: 請在電腦瀏覽器開啟以獲得最佳體驗"
            
    # ========== 說明選單 ==========
    else:
        reply = f"🤖 【StockAI Line Bot V3.0】\n"
        reply += "\n📋 指令清單:\n"
        reply += "-" * 30 + "\n"
        reply += "【選股功能】\n"
        reply += "• V30 → 🔥純技術分析 (40%報酬)\n"
        reply += "• 推薦 → 🧠V30篩選+AI評分 (實驗)\n"
        reply += "• 2330 → 個股診斷\n"
        reply += "\n【V32 新功能】✨\n"
        reply += "• dashboard → 開啟視覺化儀表板\n"
        reply += "\n【V30 參數調整】\n"
        reply += "• 設定停損 5 (停損5%)\n"
        reply += "• 設定停利 20 (停利20%)\n"
        reply += "• 設定停利 0 (不停利)\n"
        reply += "• 查看設定\n"
        reply += "\n【AI 設定】\n"
        reply += "• 設定信心 60 (AI門檻60%)\n"
        reply += "-" * 30 + "\n"
        reply += "💡 建議優先使用「V30」\n"
        reply += "⚠️ AI功能僅供參考"
    
    # 使用 Line Bot SDK v3 回覆訊息
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[V3TextMessage(text=reply)]
            )
        )


if __name__ == "__main__":
    print("=" * 60)
    print("[START] Line Bot V3.0 啟動中 (V30策略增強版)")
    print(f"[MODEL] 模型狀態: {'已載入' if model else '未載入'}")
    print(f"[INFO] 主要策略: V30 純技術分析 (40%報酬實績)")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5000, debug=False)