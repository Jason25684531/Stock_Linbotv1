"""策略模組 - V31 混合策略實作

此模組包含：
- V31 混合策略（V30 硬篩選 + XGBoost 智慧排名）
- V30 純技術面策略
- 格式化輸出函數
- 資金管理建議

🔥 V31 Optimization (2026-01):
- 放寬停損至 10%（提升波動容忍度）
- 提高停利至 20%（提升盈虧比）
- 加入市場趨勢過濾器（熊市暫停買進）
"""
from typing import Dict, List, Optional, Tuple, Any
import pandas as pd
import joblib
import os
from config import Config

# ============================================
# 💾 快取變數 (Module-level cache)
# ============================================
_cached_model: Optional[Any] = None
_cached_features: Optional[List[str]] = None


# ============================================
# 🔧 Helper Functions
# ============================================

def check_market_trend(date_str: str) -> Optional[str]:
    """檢查市場趨勢
    
    Args:
        date_str: 日期字串 (YYYY-MM-DD)
    
    Returns:
        'BULL' | 'BEAR' | 'NEUTRAL' | None (失敗時)
    """
    try:
        from tool.db_helper import get_market_trend
        return get_market_trend(date_str)
    except Exception as e:
        print(f"⚠️ 市場趨勢檢查失敗: {e}")
        return None


def _load_v31_model() -> Tuple[Optional[Any], Optional[List[str]]]:
    """載入 V31 混合策略模型（帶快取與特徵驗證）
    
    Returns:
        Tuple[model, features]: 模型物件和特徵清單，失敗時返回 (None, None)
    """
    global _cached_model, _cached_features
    
    if _cached_model is not None:
        return _cached_model, _cached_features
    
    # 嘗試多個路徑
    paths = [Config.MODEL_PATH, 'stock_ai_model.pkl', os.path.join('ML_Data', 'pkl', 'stock_ai_model.pkl')]
    
    for path in paths:
        if os.path.exists(path):
            try:
                data = joblib.load(path)
                # V31 格式：包含 model 和 features
                if isinstance(data, dict) and 'model' in data:
                    _cached_model = data['model']
                    _cached_features = data.get('features', [])
                    
                    # 🔥 關鍵：驗證特徵列表
                    if _cached_features:
                        # 檢查模型特徵與 Config 特徵是否一致
                        config_features = set(Config.FEATURES)
                        model_features = set(_cached_features)
                        
                        if config_features != model_features:
                            print("⚠️ 警告：模型特徵與 Config 不一致！")
                            print(f"   Config 特徵: {Config.FEATURES}")
                            print(f"   模型特徵: {_cached_features}")
                            print("   → 將使用模型儲存的特徵列表（確保維度一致）")
                        
                        # 無論如何，都使用模型儲存的特徵列表（避免維度錯誤）
                        print(f"✅ V31 模型載入成功: {path}")
                        print(f"📋 使用特徵列表: {_cached_features}")
                    else:
                        print(f"✅ 模型載入成功，但未找到特徵列表，使用 Config 預設")
                        _cached_features = Config.FEATURES
                    
                    return _cached_model, _cached_features
                else:
                    # 舊格式：直接是模型，使用 Config 定義的特徵
                    _cached_model = data
                    _cached_features = Config.FEATURES
                    print(f"⚠️ 載入舊版模型格式: {path}")
                    print(f"📋 使用 Config 預設特徵: {_cached_features}")
                    return _cached_model, _cached_features
            except Exception as e:
                print(f"⚠️ 載入模型失敗 ({path}): {e}")
    
    print("❌ 未找到可用的模型文件")
    return None, None


def get_best_stocks_v31_hybrid(df: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
    """🔥 V31 混合策略選股（V30 篩選 + ML 智慧排名）
    
    🆕 V31 Optimization: 加入市場趨勢過濾器
    
    流程：
    1. 檢查市場趨勢（如果是熊市則暫停買進）
    2. V30 硬篩選（均線、量能、RSI）
    3. 計算比例特徵（與訓練時一致）
    4. ML 預測機率評分
    5. 依評分排序，返回 Top N
    
    Args:
        df: DataFrame，包含股票資料
        top_n: 返回前幾名（預設 5）
        
    Returns:
        DataFrame: 帶有 ai_score 的推薦股票清單
    """
    if df.empty:
        return pd.DataFrame()
    
    # ============================================
    # 🆕 Step 0: 市場趨勢過濾器（V31 Optimization）
    # ============================================
    date_str = df['trade_date'].max()
    if hasattr(date_str, 'strftime'):
        date_str = date_str.strftime('%Y-%m-%d')
    else:
        date_str = str(date_str)
    
    market_trend = check_market_trend(date_str)
    
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
    # Step 2: 載入 ML 模型
    # ============================================
    model, feature_list = _load_v31_model()
    
    if model is None:
        print("⚠️ ML 模型未載入，僅使用 V30 篩選")
        candidates['ai_score'] = 0.5  # 預設分數
        return candidates.head(top_n)
    
    # ============================================
    # Step 3: 計算比例特徵（關鍵：必須與訓練時一致）
    # ============================================
    candidates = candidates.copy()
    
    # 避免除以零
    candidates['volume'] = candidates['volume'].replace(0, 1)
    
    # 計算成交量比例（相對於資料中的平均值，簡化版）
    avg_volume = candidates['volume'].mean()
    candidates['volume_ratio'] = candidates['volume'] / avg_volume if avg_volume > 0 else 1
    candidates['volume_ratio'] = candidates['volume_ratio'].clip(0, 5)
    
    # 籌碼面比例
    if 'foreign_buy' in candidates.columns:
        candidates['foreign_ratio'] = candidates['foreign_buy'] / candidates['volume']
        candidates['foreign_ratio'] = candidates['foreign_ratio'].clip(-0.5, 0.5)
    else:
        candidates['foreign_ratio'] = 0
        
    if 'trust_buy' in candidates.columns:
        candidates['trust_ratio'] = candidates['trust_buy'] / candidates['volume']
        candidates['trust_ratio'] = candidates['trust_ratio'].clip(-0.5, 0.5)
    else:
        candidates['trust_ratio'] = 0
    
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
    """從資料庫讀取 V30 動態參數（如果有設定的話）
    
    Returns:
        dict: V30 參數字典
    """
    try:
        from sqlalchemy import create_engine, text
        
        engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)
        params = {
            'VOLUME_THRESHOLD': Config.V30_VOLUME_THRESHOLD,
            'RSI_LOW': Config.V30_RSI_LOW,
            'RSI_HIGH': Config.V30_RSI_HIGH,
            'STOP_LOSS': Config.V30_STOP_LOSS,
            'TAKE_PROFIT': Config.V30_TAKE_PROFIT,
            'MAX_HOLD_DAYS': Config.V30_MAX_HOLD_DAYS,
        }
        
        with engine.connect() as conn:
            # 讀取自訂參數
            result = conn.execute(text(
                "SELECT setting_key, setting_value FROM user_settings WHERE setting_key LIKE 'v30_%'"
            )).fetchall()
            
            for key, value in result:
                if key == 'v30_stop_loss':
                    params['STOP_LOSS'] = float(value)
                elif key == 'v30_take_profit':
                    params['TAKE_PROFIT'] = float(value)  # 0 表示不停利
                elif key == 'v30_max_hold_days':
                    params['MAX_HOLD_DAYS'] = int(value)
        
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
    V30 策略候選股票篩選器（嚴格版）
    
    🆕 V31 Optimization: 加入市場趨勢過濾器
    
    篩選條件：
    1. 市場趨勢檢查（熊市不選股）
    2. 均線排列：收盤價 > MA20 > MA60
    3. 成交量：> 300萬股
    4. RSI：40 < RSI < 70
    
    Args:
        df: DataFrame，必須包含 close_price, ma20, ma60, volume, rsi
        
    Returns:
        DataFrame: 符合 V30 條件的股票清單
    """
    if df.empty:
        return pd.DataFrame()
    
    # ============================================
    # 🆕 市場趨勢過濾器（V31 Optimization）
    # ============================================
    date_str = df['trade_date'].max()
    if hasattr(date_str, 'strftime'):
        date_str = date_str.strftime('%Y-%m-%d')
    else:
        date_str = str(date_str)
    
    market_trend = check_market_trend(date_str)
    
    if market_trend == 'BEAR':
        print(f"📉 市場趨勢偏空（{date_str}），V30 暫停選股")
        return pd.DataFrame()
    
    # 確保必要欄位存在
    required_cols = ['close_price', 'ma20', 'ma60', 'volume', 'rsi']
    for col in required_cols:
        if col not in df.columns:
            print(f"⚠️ 缺少必要欄位: {col}")
            return pd.DataFrame()
    
    # 嚴格篩選
    candidates = df[
        (df['close_price'] > df['ma20']) &           # 收盤 > MA20
        (df['ma20'] > df['ma60']) &                  # MA20 > MA60
        (df['volume'] > Config.V30_VOLUME_THRESHOLD) &  # 量能充足
        (df['rsi'] > Config.V30_RSI_LOW) &           # RSI 不過低
        (df['rsi'] < Config.V30_RSI_HIGH)            # RSI 不過高
    ].copy()
    
    # ============================================
    # 🆕 V33 Phase 2: 進階濾網 (Opt-in)
    # ============================================
    
    # 1️⃣ KD 黃金交叉濾網
    if Config.USE_KD_FILTER and not candidates.empty:
        try:
            from tool.calc_indicators import calculate_kd_full
            
            # 計算完整 KD 指標
            if all(col in candidates.columns for col in ['high_price', 'low_price']):
                k_values, d_values = calculate_kd_full(candidates)
                candidates['kd_k_temp'] = k_values
                candidates['kd_d_temp'] = d_values
                
                # 篩選條件：K > K_MIN, D > D_MIN, K > D (黃金交叉)
                kd_filter = (
                    (candidates['kd_k_temp'] > Config.KD_GOLDEN_CROSS_K_MIN) &
                    (candidates['kd_d_temp'] > Config.KD_GOLDEN_CROSS_D_MIN)
                )
                
                if Config.KD_GOLDEN_CROSS_K_OVER_D:
                    kd_filter = kd_filter & (candidates['kd_k_temp'] > candidates['kd_d_temp'])
                
                candidates = candidates[kd_filter].copy()
                candidates.drop(['kd_k_temp', 'kd_d_temp'], axis=1, inplace=True, errors='ignore')
                
                print(f"✨ KD 黃金交叉濾網啟用，剩餘 {len(candidates)} 檔")
        except Exception as e:
            print(f"⚠️ KD 濾網執行失敗: {e}")
    
    # 2️⃣ 布林通道壓縮突破濾網
    if Config.USE_BB_FILTER and not candidates.empty:
        try:
            # 檢查 bb_width 欄位是否存在
            if 'bb_width' in candidates.columns:
                # 篩選條件：通道壓縮 (寬度 < 門檻)
                bb_squeeze = candidates['bb_width'] < Config.BB_SQUEEZE_THRESHOLD
                
                # 突破方向判斷
                if Config.BB_BREAKOUT_POSITION == 'upper':
                    # 價格接近上軌 (收盤價 > MA20，趨勢向上)
                    bb_filter = bb_squeeze & (candidates['close_price'] > candidates['ma20'])
                elif Config.BB_BREAKOUT_POSITION == 'lower':
                    # 價格接近下軌 (收盤價 < MA20，超跌反彈)
                    bb_filter = bb_squeeze & (candidates['close_price'] < candidates['ma20'])
                else:
                    bb_filter = bb_squeeze  # 只篩選壓縮，不管方向
                
                candidates = candidates[bb_filter].copy()
                print(f"✨ 布林通道壓縮突破濾網啟用，剩餘 {len(candidates)} 檔")
        except Exception as e:
            print(f"⚠️ BB 濾網執行失敗: {e}")
    
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
    
    # 計算停損停利
    stop_loss = close * (1 - params['STOP_LOSS'])
    take_profit = close * (1 + params['TAKE_PROFIT']) if params['TAKE_PROFIT'] > 0 else None  # None 表示不停利
    
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
        "max_hold_days": V30_PARAMS['MAX_HOLD_DAYS'],
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
        
        # 加入資金建議
        advice, money = calculate_position_size(
            ai_prob, 
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
        msg += f"🧠 AI信心: {ai_prob:.1%}\n"
        msg += f"📊 RSI: {rsi:.1f} | MA20: {ma20:.1f}"
    
    return msg