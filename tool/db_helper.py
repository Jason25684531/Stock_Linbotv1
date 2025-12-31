"""
資料庫與設定管理統一模組
============================================
功能：
1. 資料庫連線管理
2. 設定值讀寫（防 SQL Injection）
3. 資料查詢輔助函數
"""
import pandas as pd
from sqlalchemy import create_engine, text
from config import Config


def get_db_engine():
    """獲取資料庫引擎"""
    return create_engine(Config.SQLALCHEMY_DATABASE_URI)


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
        engine = get_db_engine()
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
        engine = get_db_engine()
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


def get_latest_trade_date():
    """
    獲取資料庫中的最新交易日期
    
    Returns:
        datetime.date or None
    """
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT MAX(trade_date) FROM daily_market_data")
            ).scalar()
            if result:
                return result if hasattr(result, 'strftime') else result
    except Exception as e:
        print(f"⚠️ 獲取最新日期失敗: {e}")
    return None


def get_stock_data(stock_id=None, date_str=None):
    """
    從資料庫撈取股票資料（安全版 - 參數化查詢）
    
    Args:
        stock_id: 股票代號（None 代表全市場）
        date_str: 日期字串（None 代表最新）
    
    Returns:
        (DataFrame, 日期字串)
    """
    engine = get_db_engine()
    
    try:
        # 如果沒指定日期，抓最新的一天
        if not date_str:
            latest = get_latest_trade_date()
            if not latest:
                return pd.DataFrame(), None
            date_str = latest.strftime('%Y-%m-%d') if hasattr(latest, 'strftime') else str(latest)

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
                AND stock_id NOT IN (:bond, :market, :inverse)
            """)
            df = pd.read_sql(
                sql, 
                engine, 
                params={
                    'date': date_str, 
                    'bond': Config.BOND_SYMBOL, 
                    'market': Config.MARKET_SYMBOL, 
                    'inverse': '00632R'
                }
            )
            
        return df, date_str
        
    except Exception as e:
        print(f"❌ 資料庫查詢失敗: {e}")
        return pd.DataFrame(), None


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
        'ai_threshold': lambda v: (0 <= float(v) <= 1, "AI信心需在 0-1 之間"),
        'stop_loss': lambda v: (0 < float(v) < 1, "停損需在 0-1 之間"),
        'take_profit': lambda v: (0 <= float(v) < 1, "停利需在 0-1 之間（0表示不停利）"),
        'mode': lambda v: (v in ['conservative', 'aggressive'], "模式只能是 conservative 或 aggressive"),
        'ai_top_n': lambda v: (1 <= int(v) <= 10, "推薦數量需在 1-10 之間"),
        'max_hold_days': lambda v: (1 <= int(v) <= 60, "持有天數需在 1-60 之間"),
    }
    
    if key in validators:
        try:
            is_valid, err_msg = validators[key](value)
            return is_valid, err_msg
        except:
            return False, "參數格式錯誤"
    return True, ""


def get_market_trend(date_str):
    """
    判斷大盤趨勢
    
    Args:
        date_str: 日期字串
    
    Returns:
        'BULL' | 'BEAR' | 'NEUTRAL'
    """
    try:
        df, _ = get_stock_data(Config.MARKET_SYMBOL, date_str)
        if df.empty or 'ma20' not in df.columns or 'ma60' not in df.columns:
            return 'NEUTRAL'
        
        data = df.iloc[0]
        close = data['close_price']
        ma20 = data.get('ma20', close)
        ma60 = data.get('ma60', close)
        
        if close > ma20 and ma20 > ma60:
            return 'BULL'
        elif close < ma20 and ma20 < ma60:
            return 'BEAR'
        return 'NEUTRAL'
    except:
        return 'NEUTRAL'
