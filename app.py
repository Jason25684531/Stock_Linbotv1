"""
Line Bot 主程式 (V2.0 動態設定增強版)
============================================
功能:
1. AI 選股推薦（雙重濾網）
2. 個股查詢（含策略報告）
3. 動態參數調整（資料庫設定）
4. 模式切換（穩健/積極）
"""
import pandas as pd
from sqlalchemy import create_engine, text
import joblib
import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from config import Config

# 引入策略模組
from tool.strategy import calculate_pivot_strategy, format_strategy_message, calculate_position_size

app = Flask(__name__)

# Line 設定
line_bot_api = LineBotApi(Config.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(Config.LINE_CHANNEL_SECRET)

# 載入模型
print("🧠 正在載入 AI 模型...")
model = None
try:
    if os.path.exists(Config.MODEL_PATH):
        model = joblib.load(Config.MODEL_PATH)
    elif os.path.exists('stock_ai_model.pkl'):
        model = joblib.load('stock_ai_model.pkl')
    print("✅ 模型載入成功")
except Exception as e:
    print(f"⚠️ 模型載入失敗: {e}")


# ============================================
# 🔧 設定管理函數（安全版 - 參數化查詢）
# ============================================

def get_setting(key, default_value=None):
    """
    從資料庫讀取設定值（防 SQL Injection）
    
    Args:
        key: 設定鍵值
        default_value: 預設值
    
    Returns:
        設定值字串
    """
    try:
        engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT setting_value FROM user_settings WHERE setting_key = :key"),
                {'key': key}
            ).scalar()
        return result if result is not None else default_value
    except Exception as e:
        print(f"⚠️ 讀取設定失敗 ({key}): {e}")
        return default_value


def update_setting(key, value):
    """
    更新資料庫設定值（防 SQL Injection）
    
    Args:
        key: 設定鍵值
        value: 新設定值
    
    Returns:
        是否成功
    """
    try:
        engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)
        with engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO user_settings (setting_key, setting_value) 
                    VALUES (:key, :value)
                    ON DUPLICATE KEY UPDATE 
                        setting_value = :value,
                        updated_at = CURRENT_TIMESTAMP
                """),
                {'key': key, 'value': value}
            )
            conn.commit()
        return True
    except Exception as e:
        print(f"❌ 更新設定失敗 ({key}={value}): {e}")
        return False


def validate_setting(key, value):
    """
    驗證參數合法性
    
    Args:
        key: 設定鍵值
        value: 待驗證的值
    
    Returns:
        (是否合法, 錯誤訊息)
    """
    validators = {
        'ai_threshold': lambda v: (0 <= float(v) <= 1, "AI信心需在 0-100 之間"),
        'stop_loss': lambda v: (0 < float(v) < 1, "停損需在 0-100% 之間"),
        'take_profit': lambda v: (0 < float(v) < 1, "停利需在 0-100% 之間"),
        'mode': lambda v: (v in ['conservative', 'aggressive'], "模式只能是 conservative 或 aggressive"),
        'ai_top_n': lambda v: (1 <= int(v) <= 10, "推薦數量需在 1-10 之間"),
    }
    
    if key in validators:
        try:
            is_valid, err_msg = validators[key](value)
            return is_valid, err_msg
        except:
            return False, "參數格式錯誤"
    return True, ""



# ============================================
# 📊 核心業務邏輯
# ============================================

def get_db_data(stock_id=None, date_str=None):
    """
    從資料庫撈取資料（安全版 - 參數化查詢）
    
    Args:
        stock_id: 股票代號（None 代表全市場）
        date_str: 日期字串（None 代表最新）
    
    Returns:
        (DataFrame, 日期字串)
    """
    engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)
    
    try:
        # 如果沒指定日期，抓最新的一天
        if not date_str:
            with engine.connect() as conn:
                result = conn.execute(text("SELECT MAX(trade_date) FROM daily_market_data")).scalar()
                if not result:
                    return pd.DataFrame(), None
                date_str = result.strftime('%Y-%m-%d')

        # 參數化查詢（防 SQL Injection）
        if stock_id:
            sql = text("""
                SELECT * FROM daily_market_data 
                WHERE trade_date = :date AND stock_id = :sid
            """)
            df = pd.read_sql(sql, engine, params={'date': date_str, 'sid': stock_id})
        else:
            sql = text("""
                SELECT * FROM daily_market_data 
                WHERE trade_date = :date 
                AND stock_id NOT IN ('0050', '00679B', '00632R')
            """)
            df = pd.read_sql(sql, engine, params={'date': date_str})
            
        return df, date_str
        
    except Exception as e:
        print(f"❌ 資料庫查詢失敗: {e}")
        return pd.DataFrame(), None


def get_ai_recommendation():
    """
    AI 推薦邏輯（V2.0 動態參數版）
    
    Returns:
        推薦訊息字串
    """
    if model is None: 
        return "⚠️ AI 模型維護中..."
    
    try:
        # 1. 讀取動態設定
        mode = get_setting('mode', 'conservative')
        ai_threshold = float(get_setting('ai_threshold', '0.5'))
        ai_top_n = int(get_setting('ai_top_n', '5'))
        
        # 根據模式選擇成交量門檻
        if mode == 'aggressive':
            volume_filter = int(get_setting('volume_filter_aggressive', '1000000'))
        else:
            volume_filter = int(get_setting('volume_filter_conservative', '2000000'))
        
        use_ma20 = get_setting('use_ma20_filter', 'true') == 'true'
        
        # 2. 撈取最新資料
        df, date_str = get_db_data()
        if df.empty: 
            return "💤 今日無資料"

        # 3. 動態濾網
        if 'ma20' not in df.columns and use_ma20:
            return "⚠️ 資料庫缺 MA20，請執行 tool/calc_indicators.py"
        
        # 基礎過濾：成交量
        candidates = df[df['volume'] > volume_filter].copy()
        
        # 進階過濾：月線（僅穩健模式）
        if use_ma20 and mode == 'conservative':
            candidates = candidates[candidates['close_price'] > candidates['ma20']]
        
        if candidates.empty: 
            return f"🐢 今日無符合條件的股票\n模式: {mode} | 門檻: {int(ai_threshold*100)}%"

        # 4. AI 預測
        for f in Config.FEATURES:
            if f not in candidates.columns: 
                candidates[f] = 0
            
        X = candidates[Config.FEATURES].fillna(0)
        candidates['prob'] = model.predict_proba(X)[:, 1]
        
        # 5. 篩選 & 排序
        high_conf = candidates[candidates['prob'] >= ai_threshold]
        if high_conf.empty:
            return f"⚠️ 無股票信心 > {int(ai_threshold*100)}%\n請考慮降低門檻或切換積極模式"
        
        picks = high_conf.sort_values('prob', ascending=False).head(ai_top_n)
        
        # 6. 生成訊息
        mode_emoji = "🛡️" if mode == "conservative" else "😈"
        msg = f"{mode_emoji} 【AI 選股 - {mode.upper()}】\n"
        msg += f"📅 {date_str} | 🎯 門檻 {int(ai_threshold*100)}%\n"
        msg += "-" * 30 + "\n"
        
        for idx, (_, row) in enumerate(picks.iterrows(), 1):
            msg += f"{idx}. {row['stock_id']} (${row['close_price']:.2f})\n"
            msg += f"   🧠 {row['prob']:.1%} | RSI {row['rsi']:.1f}\n"
            
            # 顯示籌碼（可選）
            if get_setting('enable_chips_display', 'true') == 'true':
                foreign = int(row.get('foreign_buy', 0) / 1000)  # 轉張
                if abs(foreign) > 0:
                    chip_emoji = "🔴" if foreign > 0 else "🟢"
                    msg += f"   {chip_emoji} 外資 {foreign:+,} 張\n"
        
        msg += "-" * 30 + "\n"
        msg += f"💡 輸入股號查看詳細策略"
        
        return msg
        
    except Exception as e:
        import traceback
        print(f"❌ AI 推薦失敗: {e}")
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
        df, date_str = get_db_data(stock_id=stock_id)
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
        
        if enable_strategy:
            # 完整版：含樞紐點策略
            strat_res = calculate_pivot_strategy(
                high=float(row['high_price']),
                low=float(row['low_price']),
                close=float(row['close_price']),
                ai_prob=prob
            )
            
            # 生成完整報告
            msg = format_strategy_message(
                stock_id, 
                row['trade_date'], 
                row['close_price'], 
                prob, 
                strat_res,
                extra_data=row  # 包含籌碼資料
            )
            
            # 加入資金建議
            advice, money = calculate_position_size(
                prob, 
                row.get('pe_ratio', 0), 
                capital=1000000
            )
            msg += f"\n💰 資金建議: {advice}"
            if money > 0:
                msg += f" (${money:,})"
            
        else:
            # 簡化版：基本資訊
            price = row['close_price']
            ma20 = row.get('ma20', 0)
            ma60 = row.get('ma60', 0)
            rsi = row.get('rsi', 0)
            
            # 趨勢判斷
            if price > ma20 and ma20 > ma60: 
                trend = "🔴 多頭排列"
            elif price < ma20: 
                trend = "🟢 空頭/回檔"
            else: 
                trend = "⚪ 盤整"
            
            msg = f"🎫 {stock_id} 個股診斷\n"
            msg += f"📅 {date_str}\n"
            msg += f"💲 收盤: {price:.2f}\n"
            msg += f"📈 趨勢: {trend}\n"
            msg += f"🧠 AI信心: {prob:.1%}\n"
            msg += f"📊 RSI: {rsi:.1f} | MA20: {ma20:.1f}"
        
        return msg
        
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
        mode = get_setting('mode', 'conservative')
        ai_threshold = float(get_setting('ai_threshold', '0.5'))
        ai_top_n = int(get_setting('ai_top_n', '5'))
        stop_loss = float(get_setting('stop_loss', '0.08'))
        take_profit = float(get_setting('take_profit', '0.20'))
        
        mode_name = "🛡️ 穩健" if mode == 'conservative' else "😈 積極"
        
        msg = "⚙️ 【當前設定】\n"
        msg += "-" * 30 + "\n"
        msg += f"策略模式: {mode_name}\n"
        msg += f"AI 門檻: {int(ai_threshold*100)}%\n"
        msg += f"推薦數量: 前 {ai_top_n} 名\n"
        msg += f"停損點: {int(stop_loss*100)}%\n"
        msg += f"停利點: {int(take_profit*100)}%\n"
        msg += "-" * 30 + "\n"
        msg += "💡 可用指令:\n"
        msg += "• 切換積極 / 切換穩健\n"
        msg += "• 設定信心 60 (設定為60%)\n"
        msg += "• 設定推薦 3 (改為推薦前3名)"
        
        return msg
    except Exception as e:
        return f"❌ 讀取設定失敗: {e}"



# ============================================
# 🌐 Flask 路由
# ============================================

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'


@handler.add(MessageEvent, message=TextMessage)
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
            
    elif msg_text.startswith("設定推薦"):
        try:
            value_str = msg_text.replace("設定推薦", "").strip()
            value = int(value_str)
            
            is_valid, err_msg = validate_setting('ai_top_n', str(value))
            if not is_valid:
                reply = f"❌ {err_msg}"
            elif update_setting('ai_top_n', str(value)):
                reply = f"📊 推薦數量已設為前 {value} 名"
            else:
                reply = "❌ 設定失敗"
        except ValueError:
            reply = "❌ 格式錯誤\n正確用法：設定推薦 3"
            
    elif msg_text == "查看設定":
        reply = get_settings_info()
        
    # ========== 核心功能指令 ==========
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
            
    # ========== 說明選單 ==========
    else:
        mode = get_setting('mode', 'conservative')
        mode_emoji = "🛡️" if mode == 'conservative' else "😈"
        
        reply = f"🤖 【StockAI Line Bot】\n"
        reply += f"當前模式: {mode_emoji}\n"
        reply += "\n📋 指令清單:\n"
        reply += "-" * 30 + "\n"
        reply += "【選股功能】\n"
        reply += "• 推薦 → AI 選股推薦\n"
        reply += "• 2330 → 個股診斷\n"
        reply += "\n【設定調整】\n"
        reply += "• 切換積極 / 切換穩健\n"
        reply += "• 設定信心 60\n"
        reply += "• 設定推薦 3\n"
        reply += "• 查看設定\n"
        reply += "-" * 30 + "\n"
        reply += "💡 試試看輸入「推薦」"
        
    line_bot_api.reply_message(
        event.reply_token, 
        TextSendMessage(text=reply)
    )


if __name__ == "__main__":
    print("=" * 60)
    print("🚀 Line Bot 啟動中...")
    print(f"📋 模型狀態: {'✅ 已載入' if model else '❌ 未載入'}")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5000, debug=False)