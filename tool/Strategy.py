import pandas as pd

def calculate_pivot_strategy(high, low, close, ai_prob):
    """
    計算樞紐點戰術 (Pivot Point)
    """
    pivot = (high + low + close) / 3
    r1 = (2 * pivot) - low
    s1 = (2 * pivot) - high
    
    # AI 訊號解讀
    signal = "😐 震盪 (Wait)"
    if ai_prob > 0.6:
        signal = "🚀 強力看漲 (Strong Buy)"
    elif ai_prob > 0.53:
        signal = "📈 偏多操作 (Buy)"
    elif ai_prob < 0.4:
        signal = "🐻 偏空看待 (Sell)"
        
    return {
        "pivot": pivot,
        "r1": r1,
        "s1": s1,
        "signal": signal
    }

def format_strategy_message(stock_id, trade_date, close_price, ai_prob, strat_res, extra_data=None):
    """
    格式化輸出戰術報告 (增加籌碼面)
    extra_data: 傳入 row 資料，包含 foreign_buy, trust_buy 等
    """
    date_str = pd.to_datetime(trade_date).strftime('%Y-%m-%d')
    
    msg = f"🎫 {stock_id} 戰術分析 ({date_str})\n"
    msg += f"💲 收盤: {close_price}\n"
    msg += f"🧠 AI 信心: {ai_prob:.1%}\n"
    
    # 🟢 [新增] 籌碼面分析
    if extra_data is not None:
        # 單位換算：股 -> 張 (除以 1000)
        f_buy = extra_data.get('foreign_buy', 0) / 1000
        t_buy = extra_data.get('trust_buy', 0) / 1000
        
        # 判斷多空
        f_emoji = "🔴" if f_buy > 0 else "🟢"
        t_emoji = "🔴" if t_buy > 0 else "🟢"
        
        msg += f"--------------------\n"
        msg += f"🏛️ 外資: {f_emoji} {int(f_buy):,} 張\n"
        msg += f"🏦 投信: {t_emoji} {int(t_buy):,} 張\n"
        
        if f_buy > 0 and t_buy > 0:
            msg += "🔥 籌碼: 土洋合買 (強)\n"
        elif f_buy < 0 and t_buy < 0:
            msg += "🧊 籌碼: 雙雙提款 (弱)\n"
        elif t_buy > 0:
             msg += "👀 籌碼: 投信認養\n"
    
    msg += f"--------------------\n"
    msg += f"🎯 訊號: {strat_res['signal']}\n"
    msg += f"🛡️ 支撐 (S1): {strat_res['s1']:.2f}\n"
    msg += f"⚔️ 壓力 (R1): {strat_res['r1']:.2f}\n"
    msg += f"--------------------\n"
    
    # 簡單建議
    if ai_prob > 0.53:
        msg += "💡 建議: AI 看好，回測支撐 S1 可佈局"
    else:
        msg += "💡 建議: 信心不足，觀望或區間操作"
        
    return msg

def calculate_position_size(prob, peg, capital=100000):
    """
    資金控管建議 (V20 放寬版)
    """
    # 🟢 [修正] 放寬 PEG 門檻，不然永遠都是觀望
    # 只要 PEG < 2.5 且 信心 > 50% 就給過
    if prob > 0.5 and peg < 2.5:
        # 凱利公式簡化版 (Kelly Criterion Lite)
        # f = p - q (贏率 - 輸率)
        # 這裡我們保守一點，只用一半的凱利值
        f = (prob - (1-prob)) * 0.5
        
        # 限制最大單筆 20%
        f = max(0, min(f, 0.2)) 
        
        amount = int(capital * f)
        
        if f >= 0.15:
            return "🔥 重倉出擊 (15%~20%)", amount
        elif f >= 0.05:
            return "📈 建立基本倉 (5%~10%)", amount
        else:
             return "🤏 試單 (1%~5%)", amount
    else:
        reason = "PEG太高" if peg >= 2.5 else "信心不足"
        return f"☕ 觀望 ({reason})", 0