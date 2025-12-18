# strategy.py
# 專門負責：樞紐點計算、買賣訊號判斷、文字格式化

def calculate_pivot_strategy(high, low, close, ai_prob):
    """
    輸入：昨日高、低、收，以及 AI 信心度
    輸出：包含支撐壓力與操作建議的字典
    """
    try:
        # 1. 數學計算 (樞紐點)
        p = (high + low + close) / 3
        r1 = (2 * p) - low
        s1 = (2 * p) - high
        
        # 2. 策略判斷
        strategy = ""
        buy_price = 0
        sell_price = 0
        note = ""
        
        if ai_prob >= 0.60:
            strategy = "🔥 強力看多"
            buy_price = close   # 強勢股直接看收盤或開盤
            sell_price = r1
            note = "建議: 開盤或回檔至 S1 佈局"
        elif ai_prob >= 0.50:
            strategy = "📈 偏多震盪"
            buy_price = s1      # 掛低一點買
            sell_price = r1
            note = "建議: 等回測 S1 支撐再進場"
        else:
            strategy = "😐 觀望/偏空"
            buy_price = s1
            sell_price = p      # 反彈到中軸就跑
            note = "建議: 暫時觀望，勿輕易進場"

        return {
            'p': p, 'r1': r1, 's1': s1,
            'strategy': strategy,
            'buy_suggest': buy_price,
            'sell_suggest': sell_price,
            'note': note
        }
    except Exception as e:
        return None

def format_strategy_message(stock_id, trade_date, close, ai_prob, strat_result):
    """
    負責把策略結果包裝成漂亮的 Line 文字訊息
    """
    ai_msg = f"{ai_prob:.1%}" if ai_prob else "AI: 無法預測"
    date_str = str(trade_date).split(' ')[0]
    
    msg = f"🎫 {stock_id} 戰術分析 ({date_str})\n"
    msg += f"💲 收盤: {close}\n"
    msg += f"🧠 AI 信心: {ai_msg}\n"
    msg += "-" * 20 + "\n"
    
    if strat_result:
        msg += f"🎯 訊號: {strat_result['strategy']}\n"
        msg += f"🛡️ 支撐 (S1): {strat_result['s1']:.2f}\n"
        msg += f"⚔️ 壓力 (R1): {strat_result['r1']:.2f}\n"
        msg += f"💡 {strat_result['note']}\n"
        
        # 只在看多時給出價格建議
        if ai_prob >= 0.50:
            msg += "-" * 20 + "\n"
            msg += f"💰 參考買點: {strat_result['buy_suggest']:.2f} 附近\n"
            msg += f"💵 獲利目標: {strat_result['sell_suggest']:.2f} 附近"
            
    return msg