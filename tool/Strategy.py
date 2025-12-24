# tool/strategy.py

import pandas as pd

def calculate_pivot_strategy(high, low, close, ai_prob):
    """
    計算樞紐點 (Pivot Points) 與戰術訊號
    回傳：包含 S1, R1, P, Action 的字典
    """
    # 1. 基礎 Pivot 計算
    pivot = (high + low + close) / 3
    r1 = (2 * pivot) - low
    s1 = (2 * pivot) - high
    
    # 2. 決定戰術訊號 (Action)
    # 定義：action 必須是 'BULL', 'BEAR', 'WAIT_LOW', 'WAIT_HIGH' 其中之一
    action = "WAIT_HIGH" # 預設觀望
    
    if ai_prob >= 0.50:
        # AI 看多
        if close > pivot:
            action = "BULL"      # 強力看多 (趨勢向上 + AI挺)
        else:
            action = "WAIT_LOW"  # 逢低佈局 (價格弱 + AI挺)
    else:
        # AI 看空
        if close < pivot:
            action = "BEAR"      # 偏空看待 (趨勢向下 + AI看衰)
        else:
            action = "WAIT_HIGH" # 震盪偏多 (價格強 + AI看衰)

    return {
        'pivot': pivot,
        'r1': r1,
        's1': s1,
        'action': action  # 🟢 [關鍵修正] 必須回傳這個，不然會報錯
    }

# 修改 tool/strategy.py
def calculate_position_size(win_prob, peg, capital=100000): # 
    """
    動態資金控管 (Kelly-like) - 採用嚴格 PEG 標準
    PEG < 0.75: 低估 (加分)
    PEG 0.75~1.0: 合理 (不加不減)
    PEG > 1.2: 高估 (扣分)
    """
    base_pos = 0.20 # 基礎倉位 20%
    
    # 1. 信心加成 (Prob)
    if win_prob >= 0.70: conf_score = 1.0
    elif win_prob >= 0.60: conf_score = 0.7
    elif win_prob >= 0.53: conf_score = 0.4
    else: conf_score = 0.0 # 信心不足直接不買
        
    # 2. 估值加成 (PEG) - 彼得林區選股法則
    if peg < 0.75:
        val_score = 1.2 # 🔥 超級低估，加碼買！(原本是 1.0)
        valuation_msg = "低估"
    elif peg <= 1.0:
        val_score = 1.0 # 合理價格
        valuation_msg = "合理"
    elif peg <= 1.2:
        val_score = 0.6 # 稍微偏貴，減碼買
        valuation_msg = "稍貴"
    else:
        val_score = 0.0 # ❌ 太貴了，不買
        valuation_msg = "高估"
        
    # 3. 計算最終比例
    # 如果信心夠但太貴，val_score 會把它歸零，保護你不追高
    final_ratio = base_pos * conf_score * val_score
    
    # 確保不要超過 30% (避免過度重倉)
    final_ratio = min(final_ratio, 0.30)
    
    suggested_money = int(capital * final_ratio)
    
    if final_ratio >= 0.20:
        advice = f"🔥 強力佈局 ({valuation_msg})"
    elif final_ratio >= 0.10:
        advice = f"⚖️ 一般佈局 ({valuation_msg})"
    elif final_ratio > 0:
        advice = f"👀 輕倉嘗試 ({valuation_msg})"
    else:
        advice = f"☕ 觀望 ({valuation_msg}或信心不足)"
        
    return advice, suggested_money

def format_strategy_message(stock_id, trade_date, close, ai_prob, strat_res):
    """
    將戰術結果轉為文字訊息 (Line / Debugger 通用)
    """
    # 確保 action 存在 (防呆)
    action = strat_res.get('action', 'WAIT_HIGH')
    s1 = strat_res['s1']
    r1 = strat_res['r1']
    pivot = strat_res['pivot']

    if action == 'BULL':
        signal = "🚀 強力看多 (Buy)"
        suggestion = f"建議於支撐 {s1:.1f} 附近佈局"
    elif action == 'BEAR':
        signal = "🐻 偏空/觀望 (Sell/Wait)"
        suggestion = "趨勢向下，請勿接刀，空手者觀望"
    elif action == 'WAIT_LOW':
        signal = "⚠️ 逢低佈局? (Wait)"
        suggestion = f"AI 看好但價格弱，若站回 {pivot:.1f} 再進場"
    else: 
        signal = "😐 震盪偏多 (Wait)"
        suggestion = "AI 信心不足，雖然價格強，建議觀望"

    msg = (
        f"🎫 {stock_id} 戰術分析 ({pd.to_datetime(trade_date).date()})\n"
        f"💲 收盤: {close}\n"
        f"🧠 AI 信心: {ai_prob:.1%} (PEG過濾已啟用)\n"
        f"--------------------\n"
        f"🎯 訊號: {signal}\n"
        f"🛡️ 買點參考 (S1): {s1:.2f}\n"
        f"⚔️ 賣點參考 (R1): {s1 * 1.05:.2f} ~ {r1:.2f}\n"
        f"--------------------\n"
        f"💡 戰術建議: {suggestion}\n"
    )
    return msg