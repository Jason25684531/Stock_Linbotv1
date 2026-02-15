"""策略模組 - V30/V31 向後相容層

此模組為歷史相容 shim，所有核心邏輯已遷移至：
- 篩選邏輯 → tool/strategies/v31_hybrid.py (透過策略工廠)
- 模型載入 → tool/model_utils.load_model()
- 市場趨勢 → tool/db_helper.get_market_trend()

保留的公開 API：
- get_v30_candidates(df)        → 委派策略工廠
- get_best_stocks_v31_hybrid()  → V31 混合篩選 + AI 排名
- get_v30_params_from_db()      → DB 覆寫參數讀取
- calculate_v30_signal(row)     → V30 訊號計算
- format_v30_recommendation()   → Line 推播格式化
- format_v31_recommendation()   → Line 推播格式化
- format_stock_query()          → 個股查詢格式化
- format_strategy_message()     → 戰術報告格式化
- calculate_pivot_strategy()    → 樞紐點計算
"""
from typing import Dict, List, Optional, Tuple, Any
import pandas as pd
import os
from config import Config

# ============================================
# 💾 策略工廠延遲載入（避免循環依賴）
# ============================================
def _get_strategy():
    """動態取得當前策略（避免循環依賴）"""
    try:
        from tool.strategy_manager import get_active_strategy
        return get_active_strategy()
    except Exception as e:
        print(f"⚠️ 無法載入策略管理器: {e}")
        return None

# ============================================
# � 核心函式
# ============================================


def get_best_stocks_v31_hybrid(df: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
    """🔥 V31 混合策略選股（V30 篩選 + ML 智慧排名）
    
    🆕 V31 Optimization: 加入市場趨勢過濾器
    🆕 V33 Phase 2+: 加入市場情緒熔斷機制
    
    流程：
    1. 檢查市場情緒（如果過低則觸發熔斷）
    2. 檢查市場趨勢（如果是熊市則暫停買進）
    3. V30 硬篩選（均線、量能、RSI）
    4. 計算比例特徵（與訓練時一致）
    5. ML 預測機率評分
    6. 依評分排序，返回 Top N
    
    Args:
        df: DataFrame，包含股票資料
        top_n: 返回前幾名（預設 5）
        
    Returns:
        DataFrame: 帶有 ai_score 的推薦股票清單
    """
    if df.empty:
        return pd.DataFrame()
    
    # ============================================
    # Step 0: 市場趨勢過濾器
    # ============================================
    date_str = df['trade_date'].max()
    if hasattr(date_str, 'strftime'):
        date_str = date_str.strftime('%Y-%m-%d')
    else:
        date_str = str(date_str)
    
    from tool.db_helper import get_market_trend
    market_trend = get_market_trend(date_str)
    
    if market_trend == 'BEAR':
        print(f"📉 市場趨勢偏空（{date_str}），暫停買進")
        return pd.DataFrame()
    elif market_trend == 'NEUTRAL':
        print(f"⚪ 市場趨勢中性（{date_str}），謹慎操作")
    elif market_trend == 'BULL':
        print(f"📈 市場趨勢偏多（{date_str}），正常選股")
    
    # ============================================
    # Step 1: V30 硬篩選
    # ============================================
    candidates = get_v30_candidates(df)
    
    if candidates.empty:
        return pd.DataFrame()
    
    # ============================================
    # Step 2: 載入 ML 模型（使用 model_utils）
    # ============================================
    from tool.model_utils import load_model
    result = load_model('v31_hybrid')
    model = result[0] if result else None
    feature_list = result[1] if result else None
    
    if model is None:
        print("⚠️ ML 模型未載入，僅使用 V30 篩選")
        candidates['ai_score'] = 0.5  # 預設分數
        return candidates.head(top_n)
    
    # ============================================
    # Step 3: 計算比例特徵（使用共用函數）
    # 🔄 V33 Refactor: 使用 calc_indicators.calculate_ratio_features()
    # ============================================
    from tool.calc_indicators import calculate_ratio_features
    candidates = calculate_ratio_features(candidates)
    
    # ============================================
    # Step 4: ML 預測評分
    # ============================================
    # 確保特徵順序與訓練時一致
    for f in feature_list:
        if f not in candidates.columns:
            candidates[f] = 0
    
    X = candidates[feature_list].fillna(0)
    
    try:
        # 預測機率（取「會漲」的機率）
        probs = model.predict_proba(X)[:, 1]
        candidates['ai_score'] = probs
    except Exception as e:
        print(f"⚠️ ML 預測失敗: {e}")
        candidates['ai_score'] = 0.5
    
    # ============================================
    # Step 5: 排序並返回 Top N
    # ============================================
    result = candidates.sort_values('ai_score', ascending=False).head(top_n)
    
    return result


def get_v30_params_from_db() -> Dict[str, Any]:
    """從資料庫讀取 V30 策略參數
    
    Returns:
        Dict[str, Any]: 參數字典
    """
    try:
        from tool.db_helper import get_setting
        
        params = {
            'VOLUME_THRESHOLD': Config.V30_VOLUME_THRESHOLD,
            'RSI_LOW': Config.V30_RSI_LOW,
            'RSI_HIGH': Config.V30_RSI_HIGH,
            'STOP_LOSS': Config.V30_STOP_LOSS,
            'TAKE_PROFIT': Config.V30_TAKE_PROFIT,
            'MAX_HOLD_DAYS': Config.V30_MAX_HOLD_DAYS,
        }
        
        # 從 user_settings 表讀取使用者自訂值
        v30_sl = get_setting('v30_stop_loss')
        if v30_sl is not None:
            params['STOP_LOSS'] = float(v30_sl)
        
        v30_tp = get_setting('v30_take_profit')
        if v30_tp is not None:
            params['TAKE_PROFIT'] = float(v30_tp)
        
        v30_mhd = get_setting('v30_max_hold_days')
        if v30_mhd is not None:
            params['MAX_HOLD_DAYS'] = int(v30_mhd)
        
        return params
    except Exception:
        # 如果讀取失敗，使用預設值
        return {
            'VOLUME_THRESHOLD': Config.V30_VOLUME_THRESHOLD,
            'RSI_LOW': Config.V30_RSI_LOW,
            'RSI_HIGH': Config.V30_RSI_HIGH,
            'STOP_LOSS': Config.V30_STOP_LOSS,
            'TAKE_PROFIT': Config.V30_TAKE_PROFIT,
            'MAX_HOLD_DAYS': Config.V30_MAX_HOLD_DAYS,
        }


def get_v30_candidates(df: pd.DataFrame) -> pd.DataFrame:
    """
    V30/V31 策略候選股票篩選器
    
    🔥 V33 Phase 2 Refactor: 委託策略工廠動態執行篩選邏輯
    
    Args:
        df: DataFrame，必須包含 close_price, ma20, ma60, volume, rsi
        
    Returns:
        DataFrame: 符合條件的股票清單
    """
    if df.empty:
        return pd.DataFrame()
    
    # 使用策略工廠
    strategy = _get_strategy()
    
    if strategy is not None:
        try:
            return strategy.filter_candidates(df)
        except Exception as e:
            print(f"⚠️ 策略篩選執行失敗: {e}")
    
    # 極簡回退邏輯（僅在策略工廠完全不可用時啟用）
    print(f"⚠️ 策略工廠不可用，使用最小回退篩選邏輯")
    required_cols = ['close_price', 'ma20', 'ma60', 'volume', 'rsi']
    for col in required_cols:
        if col not in df.columns:
            print(f"⚠️ 缺少必要欄位: {col}")
            return pd.DataFrame()
    
    # 🔥 修復：填充 None/NaN 值以避免比較失敗
    df_clean = df.copy()
    for col in required_cols:
        df_clean[col] = pd.to_numeric(df_clean[col], errors='coerce').fillna(0)
    
    candidates = df_clean[
        (df_clean['close_price'] > df_clean['ma20']) &
        (df_clean['ma20'] > df_clean['ma60']) &
        (df_clean['volume'] > Config.V30_VOLUME_THRESHOLD) &
        (df_clean['rsi'] > Config.V30_RSI_LOW) &
        (df_clean['rsi'] < Config.V30_RSI_HIGH)
    ].copy()
    
    return candidates


def calculate_v30_signal(row, custom_params=None):
    """
    V30 簡單策略訊號判斷（均線突破 + 量能確認）
    已在回測中實現 40% 報酬率
    
    Args:
        row: 包含 close_price, ma20, ma60, volume, rsi 的資料
        custom_params: 自訂參數（可選），如不提供則使用資料庫或預設值
        
    Returns:
        dict: 訊號、停損、停利
    """
    # 使用自訂參數或從資料庫讀取
    params = custom_params if custom_params else get_v30_params_from_db()
    
    close = float(row.get('close_price', 0))
    ma20 = float(row.get('ma20', 0))
    ma60 = float(row.get('ma60', 0))
    volume = float(row.get('volume', 0))
    rsi = float(row.get('rsi', 50))
    
    # 條件檢查
    is_above_ma = close > ma20 > ma60 if ma20 > 0 and ma60 > 0 else False
    volume_ok = volume > params['VOLUME_THRESHOLD']
    rsi_ok = params['RSI_LOW'] < rsi < params['RSI_HIGH']
    
    # ============================================
    # 🛡️ V33 Phase 1+: ATR 動態停損
    # ============================================
    if Config.USE_ATR_STOP:
        atr = float(row.get('atr', 0))
        if atr > 0:
            # 🔥 ATR 動態停損：收盤價 - ATR * 乘數
            # 波動大則停損寬，波動小則停損窄
            stop_loss = close - (atr * Config.ATR_MULTIPLIER)
        else:
            # ATR 不可用時，回退到固定百分比停損
            stop_loss = close * (1 - params['STOP_LOSS'])
    else:
        # 傳統固定百分比停損
        stop_loss = close * (1 - params['STOP_LOSS'])
    
    # 計算停利（修复 NoneType 错误：确保总是返回数值）
    if params.get('TAKE_PROFIT') and params['TAKE_PROFIT'] > 0:
        take_profit = close * (1 + params['TAKE_PROFIT'])
    else:
        take_profit = 0  # 0 表示不停利（而非 None）
    
    # 訊號判斷
    conditions_met = sum([is_above_ma, volume_ok, rsi_ok])
    
    if conditions_met == 3:
        signal = "🚀 強力買進 (V30訊號)"
        signal_strength = "strong"
    elif conditions_met == 2:
        signal = "📈 偏多觀察"
        signal_strength = "moderate"
    elif conditions_met == 1:
        signal = "😐 條件不足"
        signal_strength = "weak"
    else:
        signal = "⛔ 不符條件"
        signal_strength = "none"
    
    return {
        "signal": signal,
        "signal_strength": signal_strength,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "max_hold_days": params['MAX_HOLD_DAYS'],
        "conditions": {
            "均線排列": "✅" if is_above_ma else "❌",
            "量能充足": "✅" if volume_ok else "❌",
            "RSI適中": "✅" if rsi_ok else "❌",
        }
    }


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
    格式化輸出戰術報告 (V30 增強版 - 整合均線策略)
    extra_data: 傳入 row 資料，包含 foreign_buy, trust_buy, ma20, ma60, volume, rsi 等
    """
    date_str = pd.to_datetime(trade_date).strftime('%Y-%m-%d')
    
    msg = f"🎫 {stock_id} 戰術分析 ({date_str})\n"
    msg += f"💲 收盤: {close_price}\n"
    msg += f"🧠 AI 信心: {ai_prob:.1%}\n"
    
    # 🟢 V30 策略訊號
    if extra_data is not None:
        v30_result = calculate_v30_signal(extra_data)
        msg += f"--------------------\n"
        msg += f"📊 V30 策略分析:\n"
        for cond_name, status in v30_result['conditions'].items():
            msg += f"   {status} {cond_name}\n"
        msg += f"🎯 V30訊號: {v30_result['signal']}\n"
        
        if v30_result['signal_strength'] in ['strong', 'moderate']:
            msg += f"🛡️ 停損: ${v30_result['stop_loss']:.2f} (-5%)\n"
            msg += f"🎯 停利: ${v30_result['take_profit']:.2f} (+10%)\n"
            msg += f"⏰ 最長持有: {v30_result['max_hold_days']}天\n"
    
    # 籌碼面分析
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
    msg += f"🎯 AI訊號: {strat_res['signal']}\n"
    msg += f"🛡️ 支撐 (S1): {strat_res['s1']:.2f}\n"
    msg += f"⚔️ 壓力 (R1): {strat_res['r1']:.2f}\n"
    msg += f"--------------------\n"
    
    # 綜合建議 (結合 V30 + AI)
    if extra_data is not None:
        v30_result = calculate_v30_signal(extra_data)
        if v30_result['signal_strength'] == 'strong' and ai_prob > 0.5:
            msg += "💡 建議: V30策略+AI雙確認，可積極佈局"
        elif v30_result['signal_strength'] == 'strong':
            msg += "💡 建議: V30均線突破，可建立基本倉"
        elif ai_prob > 0.53:
            msg += "💡 建議: AI 看好，回測支撐 S1 可佈局"
        else:
            msg += "💡 建議: 條件不足，觀望或區間操作"
    else:
        if ai_prob > 0.53:
            msg += "💡 建議: AI 看好，回測支撐 S1 可佈局"
        else:
            msg += "💡 建議: 信心不足，觀望或區間操作"
        
    return msg

def format_v30_recommendation(picks, date_str):
    """
    格式化 V30 策略推薦訊息
    
    Args:
        picks: list of dict，推薦股票列表
        date_str: 日期字串
    
    Returns:
        str: 格式化後的推薦訊息
    """
    if not picks:
        return f"🐢 V30策略：今日無符合條件股票\n📅 {date_str}"
    
    msg = f"🔥 【V30 純技術面推薦】\n"
    msg += f"📅 {date_str}\n"
    msg += f"🎯 目標: 獲利10-20% | 停損5%\n"
    msg += "-" * 28 + "\n"
    
    for idx, pick in enumerate(picks, 1):
        msg += f"{idx}. {pick['stock_id']} (${pick['close_price']:.2f})\n"
        msg += f"   📊 RSI {pick['rsi']:.1f} | 量 {int(pick['volume']/10000)}萬\n"
        msg += f"   🛡️ 停損 ${pick['stop_loss']:.2f} | 🎯 停利 ${pick['take_profit']:.2f}\n"
        
        # 籌碼面
        f_buy = pick.get('foreign_buy', 0) / 1000
        if abs(f_buy) > 100:
            emoji = "🔴" if f_buy > 0 else "🟢"
            msg += f"   {emoji} 外資 {int(f_buy):,} 張\n"
    
    msg += "-" * 28 + "\n"
    msg += f"⏰ 建議持有: 最長{Config.V30_MAX_HOLD_DAYS}天\n"
    msg += "⚠️ 請嚴格執行停損停利"
    
    return msg


def format_v31_recommendation(picks, date_str):
    """
    格式化 V31 混合策略推薦訊息
    
    Args:
        picks: DataFrame，帶有 ai_score 的推薦股票
        date_str: 日期字串
    
    Returns:
        str: 格式化後的推薦訊息
    """
    if picks.empty:
        return f"🐢 V31策略：今日無符合條件股票\n📅 {date_str}\n\n💡 V30條件：均線多頭 + 量能>300萬 + 40<RSI<70"
    
    msg = f"🧠 【V31 混合策略推薦】\n"
    msg += f"📅 {date_str}\n"
    msg += f"🎯 目標: 獲利10-20% | 停損5%\n"
    msg += "-" * 28 + "\n"
    
    for idx, (_, row) in enumerate(picks.iterrows(), 1):
        ai_score = row.get('ai_score', 0)
        stop_loss = row['close_price'] * (1 - Config.V30_STOP_LOSS)
        take_profit = row['close_price'] * (1 + Config.V30_TAKE_PROFIT)
        
        msg += f"{idx}. {row['stock_id']} (${row['close_price']:.2f})\n"
        msg += f"   🧠 AI {ai_score:.0%} | RSI {row['rsi']:.1f}\n"
        msg += f"   🛡️ 停損 ${stop_loss:.2f} | 🎯 停利 ${take_profit:.2f}\n"
    
    msg += "-" * 28 + "\n"
    msg += f"⏰ 建議持有: 最長{Config.V30_MAX_HOLD_DAYS}天\n"
    msg += "⚠️ AI僅供參考，請嚴格執行停損"
    
    return msg


def format_stock_query(stock_id, date_str, row, ai_prob, enable_strategy=True):
    """
    格式化個股查詢報告
    
    Args:
        stock_id: 股票代號
        date_str: 日期字串
        row: 股票資料 (Series 或 dict)
        ai_prob: AI 預測機率
        enable_strategy: 是否啟用完整策略報告
    
    Returns:
        str: 格式化後的查詢報告
    """
    if enable_strategy:
        # 完整版：含樞紐點策略
        strat_res = calculate_pivot_strategy(
            high=float(row['high_price']),
            low=float(row['low_price']),
            close=float(row['close_price']),
            ai_prob=ai_prob
        )
        
        # 生成完整報告
        msg = format_strategy_message(
            stock_id, 
            date_str, 
            row['close_price'], 
            ai_prob, 
            strat_res,
            extra_data=row
        )
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
        msg += f"🧠 AI信心: {ai_prob:.1%}\n"
        msg += f"📊 RSI: {rsi:.1f} | MA20: {ma20:.1f}"
    
    return msg