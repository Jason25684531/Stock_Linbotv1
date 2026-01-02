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
from tool.strategy import (
    calculate_pivot_strategy, format_strategy_message, calculate_position_size, 
    calculate_v30_signal, V30_PARAMS, get_best_stocks_v31_hybrid,
    format_v30_recommendation, format_v31_recommendation, format_stock_query
)
# 引入資料庫輔助模組
from tool.db_helper import get_setting, update_setting, validate_setting, get_stock_data

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
        v30_stop_loss = float(get_setting('v30_stop_loss', str(V30_PARAMS['STOP_LOSS'])))
        v30_take_profit = float(get_setting('v30_take_profit', str(V30_PARAMS['TAKE_PROFIT'])))
        v30_max_hold = int(get_setting('v30_max_hold_days', str(V30_PARAMS['MAX_HOLD_DAYS'])))
        
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
                    reply = f"🎯 V30停利已取消\n將持有至停損或到期（{V30_PARAMS['MAX_HOLD_DAYS']}天）"
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
            
    # ========== 說明選單 ==========
    else:
        reply = f"🤖 【StockAI Line Bot V3.0】\n"
        reply += "\n📋 指令清單:\n"
        reply += "-" * 30 + "\n"
        reply += "【選股功能】\n"
        reply += "• V30 → 🔥純技術分析 (40%報酬)\n"
        reply += "• 推薦 → 🧠V30篩選+AI評分 (實驗)\n"
        reply += "• 2330 → 個股診斷\n"
        reply += "\n【V30 參數調整】✨NEW\n"
        reply += "• 設定停損 5 (停損5%)\n"
        reply += "• 設定停利 20 (停利20%)\n"
        reply += "• 設定停利 0 (不停利)\n"
        reply += "• 查看設定\n"
        reply += "\n【AI 設定】\n"
        reply += "• 設定信心 60 (AI門檻60%)\n"
        reply += "-" * 30 + "\n"
        reply += "💡 建議優先使用「V30」\n"
        reply += "⚠️ AI功能僅供參考"
        
    line_bot_api.reply_message(
        event.reply_token, 
        TextSendMessage(text=reply)
    )


if __name__ == "__main__":
    print("=" * 60)
    print("🚀 Line Bot V3.0 啟動中 (V30策略增強版)")
    print(f"📋 模型狀態: {'✅ 已載入' if model else '❌ 未載入'}")
    print(f"💡 主要策略: V30 純技術分析 (40%報酬實績)")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5000, debug=False)